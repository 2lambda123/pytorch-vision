import math
from functools import partial
from typing import Iterable, List, Optional, Callable, Tuple, Dict, Union

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models.optical_flow.raft as raft
from torch import Tensor
from torchvision.models._api import WeightsEnum
from torchvision.models.optical_flow._utils import make_coords_grid, grid_sample, upsample_flow
from torchvision.ops import Conv2dNormActivation

all = (
    "CREStereo",
    "CREStereo_Weights",
    "CREStereo_B_Weights",
    "crestereo_b",
)


class ConvexMaskPredictor(nn.Module):
    def __init__(
        self,
        *,
        in_channels: int,
        hidden_size: int,
        upsample_factor: int,
        multiplier: float = 0.25,
    ) -> None:

        super().__init__()
        self.mask_head = nn.Sequential(
            Conv2dNormActivation(in_channels, hidden_size, norm_layer=None, kernel_size=3),
            nn.Conv2d(hidden_size, upsample_factor ** 2 * 9, 1, padding=0),
        )

        self.multiplier = multiplier

    def forward(self, x: Tensor) -> Tensor:
        x = self.mask_head(x) * self.multiplier
        return x


class AdaptiveGroupCorrelationLayer(nn.Module):
    """
    Container for computing various correlation types between a left and right feature map.
    This module does not contain any optimisable parameters, it's solely a collection of ops.
    We wrap in a nn.Module for torch.jit.script compatibility

    Adaptive Group Correlation operations from: https://openaccess.thecvf.com/content/CVPR2022/papers/Li_Practical_Stereo_Matching_via_Cascaded_Recurrent_Network_With_Adaptive_Correlation_CVPR_2022_paper.pdf

    Canonical reference implementation: https://github.com/megvii-research/CREStereo/blob/master/nets/corr.py
    """

    def __init__(
        self,
        attention_module: Optional[nn.Module] = None,
        groups: int = 4,
        search_window_1d: Tuple[int, int] = (1, 9),
        search_dilate_1d: Tuple[int, int] = (1, 1),
        search_window_2d: Tuple[int, int] = (3, 3),
        search_dilate_2d: Tuple[int, int] = (1, 1),
    ) -> None:
        super().__init__()
        self.attention_module = attention_module

        if not np.prod(search_window_1d) == np.prod(search_window_2d):
            raise ValueError(
                f"The 1D and 2D windows should contain the same number of elements. "
                f"1D shape: {search_window_1d} 2D shape: {search_window_2d}"
            )

        if not np.prod(search_window_1d) % 2 == 1:
            raise ValueError(
                f"Search windows should contain an odd number of elements in them."
                f"Window of shape {search_window_1d} has {np.prod(search_window_1d)} elements."
            )

        if not any(size == 1 for size in search_window_1d):
            raise ValueError(
                f"The 1D search window should have at least one size equal to 1. 1D shape: {search_window_1d}"
            )

        if any(size == 1 for size in search_window_2d):
            raise ValueError(
                f"The 2D search window should have all dimensions greater than 1. 2D shape: {search_window_2d}"
            )

        self.search_window_1d = search_window_1d
        self.search_window_2d = search_window_2d

        self.search_dilate_1d = search_dilate_1d
        self.search_dilate_2d = search_dilate_2d

        self.groups = groups

        # two selection tables for dealing withh the small_patch argument in the forward function
        self.patch_sizes = {
            True: [self.search_window_2d for _ in range(self.groups)],
            False: [self.search_window_1d for _ in range(self.groups)],
        }

        self.dilate_sizes = {
            True: [self.search_dilate_2d for _ in range(self.groups)],
            False: [self.search_dilate_1d for _ in range(self.groups)],
        }

    def forward(
        self,
        left_features: Tensor,
        right_features: Tensor,
        flow: torch.Tensor,
        extra_offset: Union[torch.Tensor, None],
        use_small_patch: bool = False,
        iter_mode: bool = False,
    ) -> Tensor:
        if iter_mode or extra_offset is None:
            corr = self.iterative_correlation(left_features, right_features, flow, use_small_patch)
        else:
            corr = self.attention_offset_correlation(left_features, right_features, flow, extra_offset, use_small_patch)  # type: ignore
        return corr

    def _make_coords(self, feature_map: Tensor) -> Tensor:
        return make_coords_grid(feature_map.shape[0], feature_map.shape[2], feature_map.shape[3]).to(feature_map.device)

    def get_correlation(
        self,
        left_feature: Tensor,
        right_feature: Tensor,
        window_size: Tuple[int, int] = (3, 3),
        dilate: Tuple[int, int] = (1, 1),
    ) -> Tensor:
        """Function that computes a correlation product between the left and right features.

        The correlation is computed in a sliding window fashion, namely the the left features are fixed
        and for each ``(i, j)`` location we compute the correlation with a sliding window anchored in
        ``(i, j)`` from the right feature map. The sliding window selects pixels obtained in the range of the sliding
        window; i.e ``(i - window_size // 2, i + window_size // 2)`` respectively ``(j - window_size // 2, j + window_size // 2)``.
        """

        B, C, H, W = left_feature.shape

        di_y, di_x = dilate[0], dilate[1]
        pad_y, pad_x = window_size[0] // 2 * di_y, window_size[1] // 2 * di_x

        right_padded = F.pad(right_feature, (pad_x, pad_x, pad_y, pad_y), mode="replicate")
        # in order to vectorize the correlation computation over all pixel candidates
        # we create multiple shifted right images which we stack on an extra dimension
        right_padded = F.unfold(right_padded, kernel_size=(H, W), dilation=dilate).detach()
        # torch unfold returns a tensor of shape [B, flattened_values, n_selections]
        right_padded = right_padded.permute(0, 2, 1)
        # we consider rehsape back into [B, n_views, C, H, W]
        right_padded = right_padded.reshape(B, (window_size[0] * window_size[1]), C, H, W)
        # we expand the left features for broadcasting
        left_feature = left_feature.unsqueeze(1)
        # this will compute an element product of between [B, 1, C, H, W] * [B, n_views, C, H, W]
        # to obtain correlations over the pixel canditates we perform a mean on the C dimension
        correlation = torch.mean(left_feature * right_padded, dim=2, keepdim=False)
        # the final correlation tensor shape will be [B, n_views, H, W]
        # where on the i-th position of the n_views dimension we will have
        # the correlation value between the left pixel
        # and the i-th candidate on the right feature map
        return correlation

    def iterative_correlation(
        self, left_feature: Tensor, right_feature: Tensor, flow: Tensor, use_small_patch: bool = False
    ) -> Tensor:
        """Function that computes 1 pass of non-offsetted Group-Wise correlation"""
        coords = self._make_coords(left_feature)

        # we offset the coordinate grid in the flow direction
        coords = coords + flow
        coords = coords.permute(0, 2, 3, 1)
        # resample right features according to off-setted grid
        right_feature = grid_sample(right_feature, coords, mode="bilinear", align_corners=True)

        # use_small_patch is a flag by which we decide on how many axes
        # we perform candidate search. See section 3.1 ``Deformable search window`` & Figure 4 in the paper.
        patch_size_list = self.patch_sizes[use_small_patch]
        dilate_size_list = self.dilate_sizes[use_small_patch]

        # chunking the left and right feature to perform group-wise correlation
        # mechanism simillar to GroupNorm. See section 3.1 ``Group-wise correlation``.
        left_groups = torch.chunk(left_feature, self.groups, dim=1)
        right_groups = torch.chunk(right_feature, self.groups, dim=1)

        correlations = []
        # this boils down to rather than performing the correlation product
        # over the entire C dimensions, we use subsets of C to get multiple correlation sets
        for i in range(len(patch_size_list)):
            correlation = self.get_correlation(left_groups[i], right_groups[i], patch_size_list[i], dilate_size_list[i])
            correlations.append(correlation)
        final_correlations = torch.cat(correlations, dim=1)
        return final_correlations

    def attention_offset_correlation(
        self,
        left_feature: Tensor,
        right_feature: Tensor,
        flow: Tensor,
        extra_offset: Tensor,
        use_small_patch: bool = False,
    ) -> Tensor:
        """Function that computes 1 pass of offsetted Group-Wise correlation

        If the class was provided with an attention layer, the left and right feature maps
        will be passed through a transformer first
        """
        B, C, H, W = left_feature.shape

        if self.attention_module is not None:
            # prepare for transformer required input shapes
            left_feature = left_feature.permute(0, 2, 3, 1).reshape(B, H * W, C)
            right_feature = right_feature.permute(0, 2, 3, 1).reshape(B, H * W, C)
            # this can be either self attention or cross attention, hence the tupple return
            left_feature, right_feature = self.attention_module(left_feature, right_feature)
            left_feature = left_feature.reshape(B, H, W, C).permute(0, 3, 1, 2)
            right_feature = right_feature.reshape(B, H, W, C).permute(0, 3, 1, 2)

        left_groups = torch.chunk(left_feature, self.groups, dim=1)
        right_groups = torch.chunk(right_feature, self.groups, dim=1)

        num_search_candidates = self.search_window_2d[1] * self.search_window_2d[0]
        # for each pixel (i, j) we have a number of search candidates
        # thus, for each candidate we should have an X-axis and Y-axis offset value
        extra_offset = extra_offset.reshape(B, num_search_candidates, 2, H, W).permute(0, 1, 3, 4, 2)

        # see line 133 for details
        patch_size_list = self.patch_sizes[use_small_patch]
        dilate_size_list = self.dilate_sizes[use_small_patch]

        group_channels = C // self.groups
        correlations = []

        for i in range(len(patch_size_list)):
            left_group, right_group = left_groups[i], right_groups[i]
            patch_size, dilate = patch_size_list[i], dilate_size_list[i]

            di_y, di_x = dilate
            ps_y, ps_x = patch_size
            # define the search based on the window patch shape
            ry, rx = ps_y // 2 * di_y, ps_x // 2 * di_x

            # base offsets for search (i.e. where to look on the search index)
            x_grid, y_grid = torch.meshgrid(
                torch.arange(-rx, rx + 1, di_x), torch.arange(-ry, ry + 1, di_y), indexing="xy"
            )
            x_grid, y_grid = x_grid.to(flow.device), y_grid.to(flow.device)
            offsets = torch.stack((x_grid, y_grid))
            offsets = offsets.reshape(2, -1).permute(1, 0)

            for d in (0, 2, 3):
                offsets = offsets.unsqueeze(d)
            # extra offsets for search (i.e. deformed search indexes. Simillar concept to deformable convolutions)
            offsets = offsets + extra_offset

            coords = self._make_coords(left_feature) + flow
            coords = coords.permute(0, 2, 3, 1).unsqueeze(1)
            coords = coords + offsets
            coords = coords.reshape(B, -1, W, 2)

            right_group = grid_sample(right_group, coords, mode="bilinear", align_corners=True)
            # we do not need to perform any window shifting because the grid sample op
            # will return a multi-view right based on the num_search_candidates dimension in the offsets
            right_group = right_group.reshape(B, group_channels, -1, H, W)
            left_group = left_group.reshape(B, group_channels, -1, H, W)
            correlation = torch.mean(left_group * right_group, dim=1)
            correlations.append(correlation)

        final_correlation = torch.cat(correlations, dim=1)
        return final_correlation


def elu_feature_map(x: Tensor) -> Tensor:
    """Elu feature map operation from: https://arxiv.org/pdf/2006.16236.pdf"""
    return F.elu(x) + 1


class LinearAttention(nn.Module):
    """
    Linear attention operation from: https://arxiv.org/pdf/2006.16236.pdf
    Cannonical implementation reference: https://github.com/idiap/fast-transformers/blob/master/fast_transformers/attention/linear_attention.py
    LoFTR implementation reference: https://github.com/zju3dv/LoFTR/blob/2122156015b61fbb650e28b58a958e4d632b1058/src/loftr/loftr_module/linear_attention.py
    """

    def __init__(self, eps: float = 1e-6, feature_map_fn: Callable[[Tensor], Tensor] = elu_feature_map) -> None:
        super().__init__()
        self.eps = eps
        self.feature_map_fn = feature_map_fn

    def forward(
        self,
        queries: Tensor,
        keys: Tensor,
        values: Tensor,
        q_mask: Optional[Tensor] = None,
        kv_mask: Optional[Tensor] = None,
    ) -> Tensor:
        """
        Args:
            queries (torch.Tensor): [N, S1, H, D]
            keys (torch.Tensor): [N, S2, H, D]
            values (torch.Tensor): [N, S2, H, D]
            q_mask (torch.Tensor): [N, S1] (optional)
            kv_mask (torch.Tensor): [N, S2] (optional)
        Returns:
            queried_values (torch.Tensor): [N, S1, H, D]
        """
        queries = self.feature_map_fn(queries)
        keys = self.feature_map_fn(keys)

        if q_mask is not None:
            queries = queries * q_mask[:, :, None, None]
        if kv_mask is not None:
            keys = keys * kv_mask[:, :, None, None]
            values = values * kv_mask[:, :, None, None]

        # mitigates fp16 overflows
        values_length = values.shape[1]
        values = values / values_length
        kv = torch.einsum("NSHD, NSHV -> NHDV", keys, values)
        z = 1 / (torch.einsum("NLHD, NHD -> NLH", queries, keys.sum(dim=1)) + self.eps)
        # rescale at the end to account for fp16 mitigation
        queried_values = torch.einsum("NLHD, NHDV, NLH -> NLHV", queries, kv, z) * values_length
        return queried_values


class SoftmaxAttention(nn.Module):
    """
    A simple softmax attention  operation
    LoFTR implementation reference: https://github.com/zju3dv/LoFTR/blob/2122156015b61fbb650e28b58a958e4d632b1058/src/loftr/loftr_module/linear_attention.py
    """

    def __init__(self, dropout: float = 0.0) -> None:
        super().__init__()
        self.dropout = nn.Dropout(dropout) if dropout else nn.Identity()

    def forward(
        self,
        queries: Tensor,
        keys: Tensor,
        values: Tensor,
        q_mask: Optional[Tensor] = None,
        kv_mask: Optional[Tensor] = None,
    ) -> Tensor:
        """
        Computes classical softmax full-attention between all queries and keys.

        Args:
            queries (torch.Tensor): [N, S1, H, D]
            keys (torch.Tensor): [N, S2, H, D]
            values (torch.Tensor): [N, S2, H, D]
            q_mask (torch.Tensor): [N, S1] (optional)
            kv_mask (torch.Tensor): [N, S2] (optional)
        Returns:
            queried_values: [N, S1, H, D]
        """

        scale_factor = 1.0 / queries.shape[3] ** 0.5  # irsqrt(D) scaling
        queries = queries * scale_factor

        qk = torch.einsum("NLHD, NSHD -> NLSH", queries, keys)
        if kv_mask is not None and q_mask is not None:
            qk.masked_fill_(~(q_mask[:, :, None, None] * kv_mask[:, None, :, None]), float("-inf"))

        attention = torch.softmax(qk, dim=2)
        attention = self.dropout(attention)

        queried_values = torch.einsum("NLSH, NSHD -> NLHD", attention, values)
        return queried_values


class PositionalEncodingSine(nn.Module):
    """
    Sinusoidal positonal encodings

    Using the scaling term from https://github.com/megvii-research/CREStereo/blob/master/nets/attention/position_encoding.py

    Unlike cannonical implementations: https://github.com/facebookresearch/detr/blob/8a144f83a287f4d3fece4acdf073f387c5af387d/models/position_encoding.py#L28-L48
    This implementation of positional encodings interleaves the X-axis and Y-axis signals on the channel dimension
    instead of concatennating them. This result in attention heads that attend to
    """

    def __init__(self, dim_model: int) -> None:
        super().__init__()
        self.dim_model = dim_model
        self.scale_factor = -math.log(10_000) / (dim_model // 2)

    def forward(self, x: Tensor) -> Tensor:
        """
        Args:
            x: [B, C, H, W]
        """
        torch._assert(
            len(x.shape) == 4,
            f"PositionalEncodingSine requires a 4-D dimensional input. Provided tensor is of shape {x.shape}",
        )

        B, C, H, W = x.shape

        coords = torch.ones(size=(H, W), dtype=x.dtype, device=x.device)
        positions_y = coords.cumsum(0).unsqueeze(0)
        positions_x = coords.cumsum(1).unsqueeze(0)

        div_term = torch.exp(
            torch.arange(0, self.dim_model // 2, step=2, dtype=x.dtype, device=x.device) * self.scale_factor
        )
        div_term = div_term[:, None, None]

        positional_embeddings = torch.zeros((self.dim_model, H, W), device=x.device, dtype=x.dtype)
        positional_embeddings[0::4, :, :] = torch.sin(positions_x * div_term)
        positional_embeddings[1::4, :, :] = torch.cos(positions_x * div_term)
        positional_embeddings[2::4, :, :] = torch.sin(positions_y * div_term)
        positional_embeddings[3::4, :, :] = torch.cos(positions_y * div_term)

        return x + positional_embeddings


class LocalFeatureEncoderLayer(nn.Module):
    """
    LoFTR transformer module from: https://arxiv.org/pdf/2104.00680.pdf
    Cannonical implementations at: https://github.com/zju3dv/LoFTR/blob/master/src/loftr/loftr_module/transformer.py
    """

    def __init__(
        self,
        *,
        dim_model: int,
        num_heads: int,
        attention_type: str = "linear",
    ) -> None:
        super().__init__()

        if attention_type not in ["linear", "softmax"]:
            raise ValueError(
                f"Unsuported attention type {attention_type}. LocalFeatureEncoderLayer supports one of ``[linear, softmax]``"
            )

        self.dim_head = dim_model // num_heads
        self.num_heads = num_heads

        # multi-head attention
        self.query_proj = nn.Linear(dim_model, dim_model, bias=False)
        self.key_proj = nn.Linear(dim_model, dim_model, bias=False)
        self.value_proj = nn.Linear(dim_model, dim_model, bias=False)
        self.attention_op = LinearAttention() if attention_type == "linear" else SoftmaxAttention()
        self.merge = nn.Linear(dim_model, dim_model, bias=False)

        # feed forward network
        self.ffn = nn.Sequential(
            nn.Linear(dim_model * 2, dim_model * 2, bias=False),
            nn.ReLU(),
            nn.Linear(dim_model * 2, dim_model, bias=False),
        )

        # norm layers
        self.attention_norm = nn.LayerNorm(dim_model)
        self.ffn_norm = nn.LayerNorm(dim_model)

    def forward(
        self, x: Tensor, source: Tensor, x_mask: Optional[Tensor] = None, source_mask: Optional[Tensor] = None
    ) -> Tensor:
        """
        Args:
            x (torch.Tensor): [B, S1, D]
            source (torch.Tensor): [B, S2, D]
            x_mask (torch.Tensor): [B, S1] (optional)
            source_mask (torch.Tensor): [B, S2] (optional)
        """
        B, S, D = x.shape
        queries, keys, values = x, source, source

        queries = self.query_proj(queries).reshape(B, S, self.num_heads, self.dim_head)
        keys = self.key_proj(keys).reshape(B, S, self.num_heads, self.dim_head)
        values = self.value_proj(values).reshape(B, S, self.num_heads, self.dim_head)

        # attention operation
        message = self.attention_op(queries, keys, values, x_mask, source_mask)
        # concatenating attention heads together before passing throught projection layer
        message = self.merge(message.reshape(B, S, D))
        message = self.attention_norm(message)

        # ffn operation
        message = self.ffn(torch.cat([x, message], dim=2))
        message = self.ffn_norm(message)

        return x + message


class LocalFeatureTransformer(nn.Module):
    """
    LoFTR transformer module from: https://arxiv.org/pdf/2104.00680.pdf
    Cannonical implementations at: https://github.com/zju3dv/LoFTR/blob/master/src/loftr/loftr_module/transformer.py
    """

    def __init__(
        self,
        *,
        dim_model: int,
        num_heads: int,
        attention_directions: List[str],
        attention_type: str = "linear",
    ) -> None:
        super(LocalFeatureTransformer, self).__init__()

        self.attention_directions = attention_directions
        for direction in attention_directions:
            if direction not in ["self", "cross"]:
                raise ValueError(
                    f"Attention direction {direction} unsupported. LocalFeatureTransformer accepts only ``attention_type`` in ``[self, cross]``."
                )

        self.layers = nn.ModuleList(
            [
                LocalFeatureEncoderLayer(dim_model=dim_model, num_heads=num_heads, attention_type=attention_type)
                for _ in attention_directions
            ]
        )

    def forward(
        self,
        left_features: Tensor,
        right_features: Tensor,
        left_mask: Optional[Tensor] = None,
        right_mask: Optional[Tensor] = None,
    ) -> Tuple[Tensor, Tensor]:
        """
        Args:
            left_features (torch.Tensor): [N, S1, D]
            right_features (torch.Tensor): [N, S2, D]
            left_mask (torch.Tensor): [N, S1] (optional)
            right_mask (torch.Tensor): [N, S2] (optional)
        Returns:
            left_features (torch.Tensor): [N, S1, D]
            right_features (torch.Tensor): [N, S2, D]
        """

        torch._assert(
            left_features.shape[2] == right_features.shape[2],
            f"left_features and right_features should have the same embedding dimensions. left_features: {left_features.shape[2]} right_features: {right_features.shape[2]}",
        )

        for idx, layer in enumerate(self.layers):
            attention_direction = self.attention_directions[idx]
            # for layer, attention_direction in zip(self.layers, self.attention_directions):

            if attention_direction == "self":
                left_features = layer(left_features, left_features, left_mask, left_mask)
                right_features = layer(right_features, right_features, right_mask, right_mask)

            elif attention_direction == "cross":
                left_features = layer(left_features, right_features, left_mask, right_mask)
                right_features = layer(right_features, left_features, right_mask, left_mask)

        return left_features, right_features


class PyramidDownsample(nn.Module):
    """
    A simple wrapper that return and Avg Pool feature pyramid based on the provided scales.
    Implicitly returns the input as well.
    """

    def __init__(self, factors: Iterable[int]) -> None:
        super().__init__()
        self.factors = factors

    def forward(self, x: torch.Tensor) -> List[Tensor]:
        results = [x]
        for factor in self.factors:
            results.append(F.avg_pool2d(x, kernel_size=factor, stride=factor))
        return results


class CREStereo(nn.Module):
    """
    Implements CREStereo from the `"Practical Stereo Matching via Cascaded Recurrent Network
    With Adaptive Correlation" <https://openaccess.thecvf.com/content/CVPR2022/papers/Li_Practical_Stereo_Matching_via_Cascaded_Recurrent_Network_With_Adaptive_Correlation_CVPR_2022_paper.pdf>`_ paper.
    Args:
        feature_encoder (raft.FeatureEncoder): Raft-like Feature Encoder module extract low-level features from inputs.
        update_block (raft.UpdateBlock): Raft-like Update Block which recursively refines a flow-map.
        flow_head (raft.FlowHead): Raft-like Flow Head which predics a flow-map from some inputs.
        self_attn_block (LocalFeatureTransformer): A Local Feature Transformer that performs self attention on the two feature maps.
        cross_attn_block (LocalFeatureTransformer): A Local Feature Transformer that performs cross attention between the two feature maps
            used in the Adaptive Group Correlation module.
        feature_downsample_rates (List[int]): The downsample rates used to build a feature pyramid from the outputs of the `feature_encoder`. Default: [2, 4]
        correlation_groups (int): In how many groups should the features be split when computer per-pixel correlation. Defaults 4.
        search_window_1d (Tuple[int, int]): The alternate search window size in the x and y directions for the 1D case. Defaults to (1, 9).
        search_dilate_1d (Tuple[int, int]): The dilation used in the `search_window_1d` when selecting pixels. Simillar to `nn.Conv2d` dilate. Defaults to (1, 1).
        search_window_2d (Tuple[int, int]): The alternate search window size in the x and y directions for the 2D case. Defaults to (3, 3).
        search_dilate_2d (Tuple[int, int]): The dilation used in the `search_window_2d` when selecting pixels. Simillar to `nn.Conv2d` dilate. Defaults to (1, 1).
    """

    def __init__(
        self,
        *,
        feature_encoder: raft.FeatureEncoder,
        update_block: raft.UpdateBlock,
        flow_head: raft.FlowHead,
        self_attn_block: LocalFeatureTransformer,
        cross_attn_block: LocalFeatureTransformer,
        feature_downsample_rates: Tuple[int, ...] = (2, 4),
        correlation_groups: int = 4,
        search_window_1d: Tuple[int, int] = (1, 9),
        search_dilate_1d: Tuple[int, int] = (1, 1),
        search_window_2d: Tuple[int, int] = (3, 3),
        search_dilate_2d: Tuple[int, int] = (1, 1),
    ) -> None:
        super().__init__()

        self.feature_encoder = feature_encoder
        self.update_block = update_block
        self.flow_head = flow_head
        self.self_attn_block = self_attn_block

        # average pooling for the feature encoder outputs
        self.downsampling_pyramid = PyramidDownsample(feature_downsample_rates)
        self.downsampling_factors: List[int] = [feature_encoder.downsample_factor]
        base_downsample_factor: int = self.downsampling_factors[0]
        for rate in feature_downsample_rates:
            self.downsampling_factors.append(base_downsample_factor * rate)

        # output resolution tracking
        self.resolutions: List[str] = [f"1 / {factor}" for factor in self.downsampling_factors]
        self.search_pixels = int(np.prod(search_window_1d))

        # flow convex upsampling mask predictor
        self.mask_predictor = ConvexMaskPredictor(
            in_channels=feature_encoder.output_dim // 2,
            hidden_size=feature_encoder.output_dim,
            upsample_factor=4,
            multiplier=0.25,
        )

        # offsets modules for offseted feature selection
        self.offset_convs = nn.ModuleDict()
        self.correlation_layers = nn.ModuleDict()

        offset_conv_layer = partial(
            Conv2dNormActivation,
            in_channels=feature_encoder.output_dim,
            out_channels=self.search_pixels * 2,
            norm_layer=None,
            activation_layer=None,
        )

        correlation_layer = partial(
            AdaptiveGroupCorrelationLayer,
            groups=correlation_groups,
            search_window_1d=search_window_1d,
            search_dilate_1d=search_dilate_1d,
            search_window_2d=search_window_2d,
            search_dilate_2d=search_dilate_2d,
        )

        # populate the dicts in top to bottom order
        # useful for iterating through torch.jit.script module given the network forward pass
        #
        # Ignore the largest resolution. We handle that separately due to torch.jit.script
        # not being to able access to runtime generated keys in ModuleDicts.
        # This way, we can keep a generic way of processing all pyramid levels but except
        # the final one

        for idx, resolution in enumerate(reversed(self.resolutions[1:])):
            # the largest resolution does use offset convolutions for sampling grid coords
            offset_conv = None if idx == len(self.resolutions) - 1 else offset_conv_layer()
            if offset_conv:
                self.offset_convs[resolution] = offset_conv
                # only the lowest resolution uses the cross attention module when computing correlation scores
                self.correlation_layers[resolution] = (
                    correlation_layer(attention_module=cross_attn_block) if idx == 0 else correlation_layer()
                )

        # correlation layer for the largest resolution
        self.max_res_correlation_layer = correlation_layer()

        # simple 2D Postional Encodings
        self.positional_encodings = PositionalEncodingSine(feature_encoder.output_dim)

    def freeze_bn(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()

    def unfreeze_bn(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.train()

    def forward(
        self, left_image: Tensor, right_image: Tensor, flow_init: Optional[Tensor], iterations: int = 10
    ) -> List[Tensor]:
        features = torch.cat([left_image, right_image], dim=0)
        features = self.feature_encoder(features)
        left_features, right_features = features.chunk(2, dim=0)

        # update block network state and input context are derived from the left feature map
        net, ctx = left_features.chunk(2, dim=1)
        net = torch.tanh(net)
        ctx = torch.relu(ctx)

        # will output lists of tensor.
        l_pyramid = self.downsampling_pyramid(left_features)
        r_pyramid = self.downsampling_pyramid(right_features)
        net_pyramid = self.downsampling_pyramid(net)
        ctx_pyramid = self.downsampling_pyramid(ctx)

        # we store in reversed order because we process the pyramid from top to bottom
        l_pyramid: Dict[str, Tensor] = {res: l_pyramid[idx] for idx, res in enumerate(self.resolutions)}
        r_pyramid: Dict[str, Tensor] = {res: r_pyramid[idx] for idx, res in enumerate(self.resolutions)}
        net_pyramid: Dict[str, Tensor] = {res: net_pyramid[idx] for idx, res in enumerate(self.resolutions)}
        ctx_pyramid: Dict[str, Tensor] = {res: ctx_pyramid[idx] for idx, res in enumerate(self.resolutions)}

        # offsets for sampling pixel candidates in the correlation ops
        offsets: Dict[str, Tensor] = {}
        for resolution, offset_conv in self.offset_convs.items():
            feature_map = l_pyramid[resolution]
            offset = offset_conv(feature_map)
            offsets[resolution] = (torch.sigmoid(offset) - 0.5) * 2.0

        # the smallest resolution is prepared for passing through self attention
        min_res = self.resolutions[-1]
        max_res = self.resolutions[0]

        B, C, MIN_H, MIN_W = l_pyramid[min_res].shape
        # add positional encodings
        l_pyramid[min_res] = self.positional_encodings(l_pyramid[min_res])
        r_pyramid[min_res] = self.positional_encodings(r_pyramid[min_res])
        # reshaping for transformer
        l_pyramid[min_res] = l_pyramid[min_res].permute(0, 2, 3, 1).reshape(B, MIN_H * MIN_W, C)
        r_pyramid[min_res] = r_pyramid[min_res].permute(0, 2, 3, 1).reshape(B, MIN_H * MIN_W, C)
        # perform self attention
        l_pyramid[min_res], r_pyramid[min_res] = self.self_attn_block(l_pyramid[min_res], r_pyramid[min_res])
        # now we need to reshape back into [B, C, H, W] format
        l_pyramid[min_res] = l_pyramid[min_res].reshape(B, MIN_H, MIN_W, C).permute(0, 3, 1, 2)
        r_pyramid[min_res] = r_pyramid[min_res].reshape(B, MIN_H, MIN_W, C).permute(0, 3, 1, 2)

        predictions: List[Tensor] = []
        flow_estimates: Dict[str, Tensor] = {}
        # we added this because of torch.script.jit
        # also, the predicition prior is always going to have the
        # spatial size of the features outputed by the feature encoder
        flow_pred_prior: Tensor = torch.empty(
            size=(B, 2, left_features.shape[2], left_features.shape[3]),
            dtype=l_pyramid[max_res].dtype,
            device=l_pyramid[max_res].device,
        )

        if flow_init is not None:
            scale = l_pyramid[max_res].shape[2] // flow_init.shape[2]
            # in CREStereo implementation they multiply with -scale instead of scale
            # this can be either a downsample or an upsample based on the cascaded inference
            # configuration
            flow_estimates[max_res] = -scale * F.interpolate(
                input=flow_init,
                size=l_pyramid[max_res].shape[2:],
                mode="bilinear",
                align_corners=True,
            )

        # when not provided with a flow prior, we construct one using the lower resolution maps
        else:
            # initialize a zero flow with the smallest resolution
            flow = torch.zeros(size=(B, 2, MIN_H, MIN_W), device=left_features.device, dtype=left_features.dtype)

            # flows from coarse resolutions are refined similarly
            # we always need to fetch the next pyramid feature map as well
            # when updating coarse resolutions, therefore we create a reversed
            # view which has its order synced with the ModuleDict keys iterator
            coarse_resolutions: List[str] = self.resolutions[::-1]  # using slicing because of torch.jit.script
            fine_grained_resolution = max_res

            # set the coarsest flow to the zero flow
            flow_estimates[coarse_resolutions[0]] = flow

            # the correlation layer ModuleDict will contain layers ordered from coarse to fine resolution
            # i.e ["1 / 16", "1 / 8", "1 / 4"]
            # the correlation layer ModuleDict has layers for all the resolutions except the fine one
            # i.e {"1 / 16": Module, "1 / 8": Module}
            # for these resolution we perform only half of the number of refinement iterations
            for idx, (resolution, correlation_layer) in enumerate(self.correlation_layers.items()):
                # compute the scale difference between the first pyramid scale and the current pyramid scale
                scale_to_base = l_pyramid[fine_grained_resolution].shape[2] // l_pyramid[resolution].shape[2]
                for it in range(iterations // 2):
                    # set wether or not we want to search on (X, Y) axes for correlation or just on X axis
                    use_small_search_patch = (it % 2) == 1
                    # we consider this a prior, therefor we do not want to back-propagate through it
                    flow_estimates[resolution] = flow_estimates[resolution].detach()

                    # corr_fn = self.get_module_from_module_dict(self.correlation_functions, resolution)
                    correlations = correlation_layer(
                        l_pyramid[resolution],  # left
                        r_pyramid[resolution],  # right
                        flow_estimates[resolution],
                        offsets[resolution],
                        use_small_search_patch,
                    )

                    # update the recurrent network state and the flow deltas
                    net_pyramid[resolution], delta_flow = self.update_block(
                        net_pyramid[resolution], ctx_pyramid[resolution], correlations, flow_estimates[resolution]
                    )

                    # the convex upsampling weights are computed w.r.t.
                    # the recurrent update state
                    up_mask = self.mask_predictor(net_pyramid[resolution])
                    flow_estimates[resolution] = flow_estimates[resolution] + delta_flow
                    # convex upsampling with the initial feature encoder downsampling rate
                    flow_pred_prior = upsample_flow(
                        flow_estimates[resolution], up_mask, factor=self.downsampling_factors[0]
                    )
                    # we then bilinear upsample to the final resolution
                    # we use a factor that's equivalent to the difference between
                    # the current downsample resolution and the base downsample resolution
                    #
                    # i.e. if a 1 / 16 flow is upsampled by 4 (base downsampling) we get a 1 / 4 flow.
                    # therefore we have to further upscale it by the difference between
                    # the current level 1 / 16 and the base level 1 / 4.
                    flow_pred = -upsample_flow(flow_pred_prior, None, factor=scale_to_base)
                    predictions.append(flow_pred)

                # when constructing the next resolution prior, we resample w.r.t
                # to the scale of the next level in the pyramid
                next_resolution = coarse_resolutions[idx + 1]
                scale_to_next = l_pyramid[next_resolution].shape[2] / flow_pred_prior.shape[2]
                # we use the flow_up_prior because this is a more accurate estimation of the true flow
                # due to the convex upsample, which resembles a learned super-resolution module.
                # this is not necessarily an upsample, it can be a downsample, based on the provided configuration
                flow_estimates[next_resolution] = -scale_to_next * F.interpolate(
                    input=flow_pred_prior,
                    size=l_pyramid[next_resolution].shape[2:],
                    mode="bilinear",
                    align_corners=True,
                )

        # finally we will be doing a full pass through the fine-grained resolution
        # this coincides with the maximum resolution

        # we keep a separate loop here in order to avoid python control flow
        # to decide how much iterations should we do based on the current resolution
        # further more, if provided with an inital flow, there is no need to generate
        # a prior estimate when moving into the final refinement stage

        for it in range(iterations):
            use_small_search_patch = (it % 2) == 1

            flow_estimates[max_res] = flow_estimates[max_res].detach()
            # we run the fine-grained resolution correlations in iterative mode
            # this means that we are using the fixed window pixel selections
            # instead of the deformed ones as with the previous steps
            correlations = self.max_res_correlation_layer(
                l_pyramid[max_res],
                r_pyramid[max_res],
                flow_estimates[max_res],
                extra_offset=None,
                use_small_patch=use_small_search_patch,
                iter_mode=True,
            )

            net_pyramid[max_res], delta_flow = self.update_block(
                net_pyramid[max_res], ctx_pyramid[max_res], correlations, flow_estimates[max_res]
            )

            up_mask = self.mask_predictor(net_pyramid[max_res])
            flow_estimates[max_res] = flow_estimates[max_res] + delta_flow
            # at the final resolution we simply do a convex upsample using the base downsample rate
            flow_pred = -upsample_flow(flow_estimates[max_res], up_mask, factor=self.downsampling_factors[0])
            predictions.append(flow_pred)

        return predictions


def _crestereo(
    *,
    weights: Optional[WeightsEnum],
    progress: bool,
    # Feature Encoder
    feature_encoder_layers: Tuple[int, int, int, int, int],
    feature_encoder_strides: Tuple[int, int, int, int],
    feature_encoder_block: Callable[..., nn.Module],
    feature_encoder_norm_layer: Callable[..., nn.Module],
    # Average Pooling Pyramid
    feature_downsample_rates: Tuple[int, ...],
    # Adaptive Correlation Layer
    corr_groups: int,
    corr_search_window_2d: Tuple[int, int],
    corr_search_dilate_2d: Tuple[int, int],
    corr_search_window_1d: Tuple[int, int],
    corr_search_dilate_1d: Tuple[int, int],
    # Flow head
    flow_head_hidden_size: int,
    # Recurrent block
    recurrent_block_hidden_state_size: int,
    recurrent_block_kernel_size: Tuple[Tuple[int, int], Tuple[int, int]],
    recurrent_block_padding: Tuple[Tuple[int, int], Tuple[int, int]],
    # Motion Encoder
    motion_encoder_corr_layers: Tuple[int, int],
    motion_encoder_flow_layers: Tuple[int, int],
    motion_encoder_out_channels: int,
    # Transformer Blocks
    num_attention_heads: int,
    num_self_attention_layers: int,
    num_cross_attention_layers: int,
    self_attention_type: str,
    cross_attention_type: str,
    **kwargs,
) -> CREStereo:

    feature_encoder = kwargs.pop("feature_encoder", None) or raft.FeatureEncoder(
        block=feature_encoder_block,
        layers=feature_encoder_layers,
        strides=feature_encoder_strides,
        norm_layer=feature_encoder_norm_layer,
    )

    if feature_encoder.output_dim % corr_groups != 0:
        raise ValueError(
            f"Final ``feature_encoder_layers`` size should be divisible by ``corr_groups`` argument."
            f"Feature encoder output size : {feature_encoder.output_dim}, Correlation groups: {corr_groups}."
        )

    motion_encoder = kwargs.pop("motion_encoder", None) or raft.MotionEncoder(
        in_channels_corr=corr_groups * int(np.prod(corr_search_window_1d)),
        corr_layers=motion_encoder_corr_layers,
        flow_layers=motion_encoder_flow_layers,
        out_channels=motion_encoder_out_channels,
    )

    out_channels_context = feature_encoder_layers[-1] - recurrent_block_hidden_state_size
    recurrent_block = kwargs.pop("recurrent_block", None) or raft.RecurrentBlock(
        input_size=motion_encoder.out_channels + out_channels_context,
        hidden_size=recurrent_block_hidden_state_size,
        kernel_size=recurrent_block_kernel_size,
        padding=recurrent_block_padding,
    )

    flow_head = kwargs.pop("flow_head", None) or raft.FlowHead(
        in_channels=out_channels_context, hidden_size=flow_head_hidden_size
    )

    update_block = raft.UpdateBlock(motion_encoder=motion_encoder, recurrent_block=recurrent_block, flow_head=flow_head)

    self_attn_block = LocalFeatureTransformer(
        dim_model=feature_encoder.output_dim,
        num_heads=num_attention_heads,
        attention_directions=["self"] * num_self_attention_layers,
        attention_type=self_attention_type,
    )

    cross_attn_block = LocalFeatureTransformer(
        dim_model=feature_encoder.output_dim,
        num_heads=num_attention_heads,
        attention_directions=["cross"] * num_cross_attention_layers,
        attention_type=cross_attention_type,
    )

    model = CREStereo(
        feature_encoder=feature_encoder,
        update_block=update_block,
        flow_head=flow_head,
        self_attn_block=self_attn_block,
        cross_attn_block=cross_attn_block,
        feature_downsample_rates=feature_downsample_rates,
        correlation_groups=corr_groups,
        search_window_1d=corr_search_window_1d,
        search_window_2d=corr_search_window_2d,
        search_dilate_1d=corr_search_dilate_1d,
        search_dilate_2d=corr_search_dilate_2d,
    )

    if weights is not None:
        model.load_state_dict(weights.get_state_dict(progress=progress))

    return model


class CREStereo_Weights(WeightsEnum):
    pass


class CREStereo_B_Weights(CREStereo_Weights):
    pass


def crestereo_b(*, weights: Optional[CREStereo_Weights] = None, progress=True, **kwargs) -> CREStereo:
    return _crestereo(
        weights=weights,
        progress=progress,
        # Feature encoder
        feature_encoder_layers=(64, 64, 96, 128, 256),
        feature_encoder_strides=(2, 1, 2, 1),
        feature_encoder_block=partial(raft.ResidualBlock, always_project=True),
        feature_encoder_norm_layer=nn.InstanceNorm2d,
        # Average pooling pyramid
        feature_downsample_rates=(2, 4),
        # Motion encoder
        motion_encoder_corr_layers=(256, 192),
        motion_encoder_flow_layers=(128, 64),
        motion_encoder_out_channels=128,
        # Recurrent block
        recurrent_block_hidden_state_size=128,
        recurrent_block_kernel_size=((1, 5), (5, 1)),
        recurrent_block_padding=((0, 2), (2, 0)),
        # Flow head
        flow_head_hidden_size=256,
        # Transformer blocks
        num_attention_heads=8,
        num_self_attention_layers=1,
        num_cross_attention_layers=1,
        self_attention_type="linear",
        cross_attention_type="linear",
        # Adaptive Correlation layer
        corr_groups=4,
        corr_search_window_2d=(3, 3),
        corr_search_dilate_2d=(1, 1),
        corr_search_window_1d=(1, 9),
        corr_search_dilate_1d=(1, 1),
    )
