import functools
from typing import Any, Callable, Dict, List, Sequence, Type, Union

import torch
from torchvision.prototype import features
from torchvision.prototype.transforms import functional as F, Transform
from torchvision.prototype.transforms._utils import query_bounding_box
from torchvision.transforms.transforms import _setup_size


class Identity(Transform):
    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        return inpt


class Lambda(Transform):
    def __init__(self, fn: Callable[[Any], Any], *types: Type):
        super().__init__()
        self.fn = fn
        self.types = types

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        if type(inpt) in self.types:
            return self.fn(inpt)
        else:
            return inpt

    def extra_repr(self) -> str:
        extras = []
        name = getattr(self.fn, "__name__", None)
        if name:
            extras.append(name)
        extras.append(f"types={[type.__name__ for type in self.types]}")
        return ", ".join(extras)


class Normalize(Transform):
    def __init__(self, mean: List[float], std: List[float]):
        super().__init__()
        self.mean = mean
        self.std = std

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        return F.normalize(inpt, mean=self.mean, std=self.std)


class GaussianBlur(Transform):
    def __init__(
        self, kernel_size: Union[int, Sequence[int]], sigma: Union[float, Sequence[float]] = (0.1, 2.0)
    ) -> None:
        super().__init__()
        self.kernel_size = _setup_size(kernel_size, "Kernel size should be a tuple/list of two integers")
        for ks in self.kernel_size:
            if ks <= 0 or ks % 2 == 0:
                raise ValueError("Kernel size value should be an odd and positive number.")

        if isinstance(sigma, float):
            if sigma <= 0:
                raise ValueError("If sigma is a single number, it must be positive.")
            sigma = (sigma, sigma)
        elif isinstance(sigma, Sequence) and len(sigma) == 2:
            if not 0.0 < sigma[0] <= sigma[1]:
                raise ValueError("sigma values should be positive and of the form (min, max).")
        else:
            raise TypeError("sigma should be a single float or a list/tuple with length 2 floats.")

        self.sigma = sigma

    def _get_params(self, sample: Any) -> Dict[str, Any]:
        sigma = torch.empty(1).uniform_(self.sigma[0], self.sigma[1]).item()
        return dict(sigma=[sigma, sigma])

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        return F.gaussian_blur(inpt, **params)


class ToDtype(Lambda):
    def __init__(self, dtype: torch.dtype, *types: Type) -> None:
        self.dtype = dtype
        super().__init__(functools.partial(torch.Tensor.to, dtype=dtype), *types)

    def extra_repr(self) -> str:
        return ", ".join([f"dtype={self.dtype}", f"types={[type.__name__ for type in self.types]}"])


class CleanupBoxes(Transform):
    def _get_params(self, sample: Any) -> Dict[str, Any]:
        bounding_boxes = query_bounding_box(sample)
        bounding_boxes_clamped = F.clamp_bounding_box(
            bounding_boxes, format=bounding_boxes.format, image_size=bounding_boxes.image_size
        )
        bounding_boxes_xywh = F.convert_bounding_box_format(
            bounding_boxes_clamped, old_format=bounding_boxes.format, new_format=features.BoundingBoxFormat.XYWH
        )
        is_valid = torch.all(bounding_boxes_xywh[..., 2:] > 0, dim=-1)

        return dict(is_valid=is_valid)

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        if isinstance(inpt, (features.Label, features.OneHotLabel, features.SegmentationMask)):
            return inpt.new_like(inpt, inpt[params["is_valid"]])  # type: ignore[arg-type]
        elif isinstance(inpt, features.BoundingBox):
            return features.BoundingBox.new_like(
                inpt,
                F.clamp_bounding_box(inpt[params["is_valid"]], format=inpt.format, image_size=inpt.image_size),
            )
        else:
            return inpt
