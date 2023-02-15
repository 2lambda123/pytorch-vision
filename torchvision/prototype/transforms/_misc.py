import collections
import warnings
from contextlib import suppress
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Type, Union

import PIL.Image

import torch
from torch.utils._pytree import tree_flatten, tree_unflatten

from torchvision import transforms as _transforms
from torchvision.prototype import datapoints
from torchvision.prototype.transforms import functional as F, Transform

from ._utils import _get_defaultdict, _setup_float_or_seq, _setup_size
from .utils import has_any, is_simple_tensor, query_bounding_box


class Identity(Transform):
    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        return inpt


class Lambda(Transform):
    def __init__(self, lambd: Callable[[Any], Any], *types: Type):
        super().__init__()
        self.lambd = lambd
        self.types = types or (object,)

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        if isinstance(inpt, self.types):
            return self.lambd(inpt)
        else:
            return inpt

    def extra_repr(self) -> str:
        extras = []
        name = getattr(self.lambd, "__name__", None)
        if name:
            extras.append(name)
        extras.append(f"types={[type.__name__ for type in self.types]}")
        return ", ".join(extras)


class LinearTransformation(Transform):
    _v1_transform_cls = _transforms.LinearTransformation

    _transformed_types = (is_simple_tensor, datapoints.Image, datapoints.Video)

    def __init__(self, transformation_matrix: torch.Tensor, mean_vector: torch.Tensor):
        super().__init__()
        if transformation_matrix.size(0) != transformation_matrix.size(1):
            raise ValueError(
                "transformation_matrix should be square. Got "
                f"{tuple(transformation_matrix.size())} rectangular matrix."
            )

        if mean_vector.size(0) != transformation_matrix.size(0):
            raise ValueError(
                f"mean_vector should have the same length {mean_vector.size(0)}"
                f" as any one of the dimensions of the transformation_matrix [{tuple(transformation_matrix.size())}]"
            )

        if transformation_matrix.device != mean_vector.device:
            raise ValueError(
                f"Input tensors should be on the same device. Got {transformation_matrix.device} and {mean_vector.device}"
            )

        if transformation_matrix.dtype != mean_vector.dtype:
            raise ValueError(
                f"Input tensors should have the same dtype. Got {transformation_matrix.dtype} and {mean_vector.dtype}"
            )

        self.transformation_matrix = transformation_matrix
        self.mean_vector = mean_vector

    def _check_inputs(self, sample: Any) -> Any:
        if has_any(sample, PIL.Image.Image):
            raise TypeError("LinearTransformation does not work on PIL Images")

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        shape = inpt.shape
        n = shape[-3] * shape[-2] * shape[-1]
        if n != self.transformation_matrix.shape[0]:
            raise ValueError(
                "Input tensor and transformation matrix have incompatible shape."
                + f"[{shape[-3]} x {shape[-2]} x {shape[-1]}] != "
                + f"{self.transformation_matrix.shape[0]}"
            )

        if inpt.device.type != self.mean_vector.device.type:
            raise ValueError(
                "Input tensor should be on the same device as transformation matrix and mean vector. "
                f"Got {inpt.device} vs {self.mean_vector.device}"
            )

        flat_inpt = inpt.reshape(-1, n) - self.mean_vector

        transformation_matrix = self.transformation_matrix.to(flat_inpt.dtype)
        output = torch.mm(flat_inpt, transformation_matrix)
        output = output.reshape(shape)

        if isinstance(inpt, (datapoints.Image, datapoints.Video)):
            output = type(inpt).wrap_like(inpt, output)  # type: ignore[arg-type]
        return output


class Normalize(Transform):
    _v1_transform_cls = _transforms.Normalize
    _transformed_types = (datapoints.Image, is_simple_tensor, datapoints.Video)

    def __init__(self, mean: Sequence[float], std: Sequence[float], inplace: bool = False):
        super().__init__()
        self.mean = list(mean)
        self.std = list(std)
        self.inplace = inplace

    def _check_inputs(self, sample: Any) -> Any:
        if has_any(sample, PIL.Image.Image):
            raise TypeError(f"{type(self).__name__}() does not support PIL images.")

    def _transform(
        self, inpt: Union[datapoints.TensorImageType, datapoints.TensorVideoType], params: Dict[str, Any]
    ) -> Any:
        return F.normalize(inpt, mean=self.mean, std=self.std, inplace=self.inplace)


class GaussianBlur(Transform):
    _v1_transform_cls = _transforms.GaussianBlur

    def __init__(
        self, kernel_size: Union[int, Sequence[int]], sigma: Union[int, float, Sequence[float]] = (0.1, 2.0)
    ) -> None:
        super().__init__()
        self.kernel_size = _setup_size(kernel_size, "Kernel size should be a tuple/list of two integers")
        for ks in self.kernel_size:
            if ks <= 0 or ks % 2 == 0:
                raise ValueError("Kernel size value should be an odd and positive number.")

        if isinstance(sigma, (int, float)):
            if sigma <= 0:
                raise ValueError("If sigma is a single number, it must be positive.")
            sigma = float(sigma)
        elif isinstance(sigma, Sequence) and len(sigma) == 2:
            if not 0.0 < sigma[0] <= sigma[1]:
                raise ValueError("sigma values should be positive and of the form (min, max).")
        else:
            raise TypeError("sigma should be a single int or float or a list/tuple with length 2 floats.")

        self.sigma = _setup_float_or_seq(sigma, "sigma", 2)

    def _get_params(self, flat_inputs: List[Any]) -> Dict[str, Any]:
        sigma = torch.empty(1).uniform_(self.sigma[0], self.sigma[1]).item()
        return dict(sigma=[sigma, sigma])

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        return F.gaussian_blur(inpt, self.kernel_size, **params)


class ToDtype(Transform):
    _transformed_types = (torch.Tensor,)

    def __init__(self, dtype: Union[torch.dtype, Dict[Type, Optional[torch.dtype]]]) -> None:
        super().__init__()
        if not isinstance(dtype, dict):
            dtype = _get_defaultdict(dtype)
        if torch.Tensor in dtype and any(cls in dtype for cls in [datapoints.Image, datapoints.Video]):
            warnings.warn(
                "Got `dtype` values for `torch.Tensor` and either `datapoints.Image` or `datapoints.Video`. "
                "Note that a plain `torch.Tensor` will *not* be transformed by this (or any other transformation) "
                "in case a `datapoints.Image` or `datapoints.Video` is present in the input."
            )
        self.dtype = dtype

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        dtype = self.dtype[type(inpt)]
        if dtype is None:
            return inpt
        return inpt.to(dtype=dtype)


class PermuteDimensions(Transform):
    _transformed_types = (is_simple_tensor, datapoints.Image, datapoints.Video)

    def __init__(self, dims: Union[Sequence[int], Dict[Type, Optional[Sequence[int]]]]) -> None:
        super().__init__()
        if not isinstance(dims, dict):
            dims = _get_defaultdict(dims)
        if torch.Tensor in dims and any(cls in dims for cls in [datapoints.Image, datapoints.Video]):
            warnings.warn(
                "Got `dims` values for `torch.Tensor` and either `datapoints.Image` or `datapoints.Video`. "
                "Note that a plain `torch.Tensor` will *not* be transformed by this (or any other transformation) "
                "in case a `datapoints.Image` or `datapoints.Video` is present in the input."
            )
        self.dims = dims

    def _transform(
        self, inpt: Union[datapoints.TensorImageType, datapoints.TensorVideoType], params: Dict[str, Any]
    ) -> torch.Tensor:
        dims = self.dims[type(inpt)]
        if dims is None:
            return inpt.as_subclass(torch.Tensor)
        return inpt.permute(*dims)


class TransposeDimensions(Transform):
    _transformed_types = (is_simple_tensor, datapoints.Image, datapoints.Video)

    def __init__(self, dims: Union[Tuple[int, int], Dict[Type, Optional[Tuple[int, int]]]]) -> None:
        super().__init__()
        if not isinstance(dims, dict):
            dims = _get_defaultdict(dims)
        if torch.Tensor in dims and any(cls in dims for cls in [datapoints.Image, datapoints.Video]):
            warnings.warn(
                "Got `dims` values for `torch.Tensor` and either `datapoints.Image` or `datapoints.Video`. "
                "Note that a plain `torch.Tensor` will *not* be transformed by this (or any other transformation) "
                "in case a `datapoints.Image` or `datapoints.Video` is present in the input."
            )
        self.dims = dims

    def _transform(
        self, inpt: Union[datapoints.TensorImageType, datapoints.TensorVideoType], params: Dict[str, Any]
    ) -> torch.Tensor:
        dims = self.dims[type(inpt)]
        if dims is None:
            return inpt.as_subclass(torch.Tensor)
        return inpt.transpose(*dims)


class SanitizeBoundingBoxes(Transform):
    # This removes boxes and their corresponding labels:
    # - small or degenerate bboxes based on min_size (this includes those where X2 <= X1 or Y2 <= Y1)
    # - boxes with any coordinate outside the range of the image (negative, or > spatial_size)

    def __init__(self, min_size: float = 1.0, labels_getter="default") -> None:
        super().__init__()

        if min_size < 1:
            raise ValueError(f"min_size must be >= 1, got {min_size}.")
        self.min_size = min_size

        self.labels_getter = labels_getter
        if labels_getter == "default":
            self._labels_getter = self._find_labels_default_heuristic
        elif callable(labels_getter):
            self._labels_getter = labels_getter
        elif isinstance(labels_getter, str):
            self._labels_getter = lambda inputs: inputs[labels_getter]
        elif labels_getter is None:
            self._labels_getter = None
        else:
            raise ValueError(
                "labels_getter should either be a str, callable, or 'default'. "
                f"Got {labels_getter} of type {type(labels_getter)}."
            )

    @staticmethod
    def _find_labels_default_heuristic(inputs):
        # Tries to find a "label" key, otherwise tries for the first key that contains "label" - case insensitive
        # Returns None if nothing is found
        candidate_key = None
        with suppress(StopIteration):
            candidate_key = next(key for key in inputs.keys() if key.lower() == "labels")
        if candidate_key is None:
            with suppress(StopIteration):
                candidate_key = next(key for key in inputs.keys() if "label" in key.lower())
        if candidate_key is None:
            raise ValueError(
                "Could not infer where the labels are in the sample. Try passing a callable as the labels_getter parameter?"
                "If there are no samples and it is by design, pass labels_getter=None."
            )
        return inputs[candidate_key]

    def forward(self, *inputs: Any) -> Any:
        inputs = inputs if len(inputs) > 1 else inputs[0]

        if isinstance(self.labels_getter, str) and not isinstance(inputs, collections.abc.Mapping):
            raise ValueError(
                f"If labels_getter is a str or 'default' (got {self.labels_getter}), "
                f"then the input to forward() must be a dict. Got {type(inputs)} instead."
            )

        if self._labels_getter is None:
            labels = None
        else:
            labels = self._labels_getter(inputs)
            if labels is not None and not isinstance(labels, torch.Tensor):
                raise ValueError(f"The labels in the input to forward() must be a tensor, got {type(labels)} instead.")

        flat_inputs, spec = tree_flatten(inputs)
        # TODO: this enforces one single BoundingBox entry.
        # Assuming this transform needs to be called at the end of *any* pipeline that has bboxes...
        # should we just enforce it for all transforms?? What are the benefits of *not* enforcing this?
        boxes = query_bounding_box(flat_inputs)

        if boxes.ndim != 2:
            raise ValueError(f"boxes must be of shape (num_boxes, 4), got {boxes.shape}")

        if labels is not None and boxes.shape[0] != labels.shape[0]:
            raise ValueError(
                f"Number of boxes (shape={boxes.shape}) and number of labels (shape={labels.shape}) do not match."
            )

        boxes = F.convert_format_bounding_box(
            boxes,
            new_format=datapoints.BoundingBoxFormat.XYXY,
        )
        ws, hs = boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]
        mask = (ws >= self.min_size) & (hs >= self.min_size) & (boxes >= 0).all(axis=-1)
        # TODO: Do we really need to check for out of bounds here? All
        # transforms should be clamping anyway, so this should never happen?
        image_h, image_w = boxes.spatial_size
        mask &= (boxes[:, 0] <= image_w) & (boxes[:, 2] <= image_w)
        mask &= (boxes[:, 1] <= image_h) & (boxes[:, 3] <= image_h)

        params = dict(mask=mask, labels=labels)
        flat_outputs = [
            # Even-though it may look like we're transforming all inputs, we don't:
            # _transform() will only care about BoundingBoxes and the labels
            self._transform(inpt, params)
            for inpt in flat_inputs
        ]

        return tree_unflatten(flat_outputs, spec)

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:

        if inpt is not None and (inpt is params["labels"] or isinstance(inpt, datapoints.BoundingBox)):
            inpt = inpt[params["mask"]]

        return inpt
