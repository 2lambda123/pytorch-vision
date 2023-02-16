import math
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

import PIL.Image
import torch

from torch.utils._pytree import tree_flatten, tree_unflatten, TreeSpec
from torchvision import datapoints, transforms as _transforms
from torchvision.transforms import _functional_tensor as _FT
from torchvision.transforms.v2 import AutoAugmentPolicy, functional as F, InterpolationMode, Transform
from torchvision.transforms.v2.functional._geometry import _check_interpolation
from torchvision.transforms.v2.functional._meta import get_spatial_size

from ._utils import _setup_fill_arg
from .utils import check_type, is_simple_tensor


class _AutoAugmentBase(Transform):
    def __init__(
        self,
        *,
        interpolation: Union[InterpolationMode, int] = InterpolationMode.NEAREST,
        fill: Union[datapoints._FillType, Dict[Type, datapoints._FillType]] = None,
    ) -> None:
        super().__init__()
        self.interpolation = _check_interpolation(interpolation)
        self.fill = _setup_fill_arg(fill)

    def _get_random_item(self, dct: Dict[str, Tuple[Callable, bool]]) -> Tuple[str, Tuple[Callable, bool]]:
        keys = tuple(dct.keys())
        key = keys[int(torch.randint(len(keys), ()))]
        return key, dct[key]

    def _flatten_and_extract_image_or_video(
        self,
        inputs: Any,
        unsupported_types: Tuple[Type, ...] = (datapoints.BoundingBox, datapoints.Mask),
    ) -> Tuple[Tuple[List[Any], TreeSpec, int], Union[datapoints._ImageType, datapoints._VideoType]]:
        flat_inputs, spec = tree_flatten(inputs if len(inputs) > 1 else inputs[0])
        needs_transform_list = self._needs_transform_list(flat_inputs)

        image_or_videos = []
        for idx, (inpt, needs_transform) in enumerate(zip(flat_inputs, needs_transform_list)):
            if needs_transform and check_type(
                inpt,
                (
                    datapoints.Image,
                    PIL.Image.Image,
                    is_simple_tensor,
                    datapoints.Video,
                ),
            ):
                image_or_videos.append((idx, inpt))
            elif isinstance(inpt, unsupported_types):
                raise TypeError(f"Inputs of type {type(inpt).__name__} are not supported by {type(self).__name__}()")

        if not image_or_videos:
            raise TypeError("Found no image in the sample.")
        if len(image_or_videos) > 1:
            raise TypeError(
                f"Auto augment transformations are only properly defined for a single image or video, "
                f"but found {len(image_or_videos)}."
            )

        idx, image_or_video = image_or_videos[0]
        return (flat_inputs, spec, idx), image_or_video

    def _unflatten_and_insert_image_or_video(
        self,
        flat_inputs_with_spec: Tuple[List[Any], TreeSpec, int],
        image_or_video: Union[datapoints._ImageType, datapoints._VideoType],
    ) -> Any:
        flat_inputs, spec, idx = flat_inputs_with_spec
        flat_inputs[idx] = image_or_video
        return tree_unflatten(flat_inputs, spec)

    def _apply_image_or_video_transform(
        self,
        image: Union[datapoints._ImageType, datapoints._VideoType],
        transform_id: str,
        magnitude: float,
        interpolation: Union[InterpolationMode, int],
        fill: Dict[Type, datapoints._FillTypeJIT],
    ) -> Union[datapoints._ImageType, datapoints._VideoType]:
        fill_ = fill[type(image)]

        if transform_id == "Identity":
            return image
        elif transform_id == "ShearX":
            # magnitude should be arctan(magnitude)
            # official autoaug: (1, level, 0, 0, 1, 0)
            # https://github.com/tensorflow/models/blob/dd02069717128186b88afa8d857ce57d17957f03/research/autoaugment/augmentation_transforms.py#L290
            # compared to
            # torchvision:      (1, tan(level), 0, 0, 1, 0)
            # https://github.com/pytorch/vision/blob/0c2373d0bba3499e95776e7936e207d8a1676e65/torchvision/transforms/functional.py#L976
            return F.affine(
                image,
                angle=0.0,
                translate=[0, 0],
                scale=1.0,
                shear=[math.degrees(math.atan(magnitude)), 0.0],
                interpolation=interpolation,
                fill=fill_,
                center=[0, 0],
            )
        elif transform_id == "ShearY":
            # magnitude should be arctan(magnitude)
            # See above
            return F.affine(
                image,
                angle=0.0,
                translate=[0, 0],
                scale=1.0,
                shear=[0.0, math.degrees(math.atan(magnitude))],
                interpolation=interpolation,
                fill=fill_,
                center=[0, 0],
            )
        elif transform_id == "TranslateX":
            return F.affine(
                image,
                angle=0.0,
                translate=[int(magnitude), 0],
                scale=1.0,
                interpolation=interpolation,
                shear=[0.0, 0.0],
                fill=fill_,
            )
        elif transform_id == "TranslateY":
            return F.affine(
                image,
                angle=0.0,
                translate=[0, int(magnitude)],
                scale=1.0,
                interpolation=interpolation,
                shear=[0.0, 0.0],
                fill=fill_,
            )
        elif transform_id == "Rotate":
            return F.rotate(image, angle=magnitude, interpolation=interpolation, fill=fill_)
        elif transform_id == "Brightness":
            return F.adjust_brightness(image, brightness_factor=1.0 + magnitude)
        elif transform_id == "Color":
            return F.adjust_saturation(image, saturation_factor=1.0 + magnitude)
        elif transform_id == "Contrast":
            return F.adjust_contrast(image, contrast_factor=1.0 + magnitude)
        elif transform_id == "Sharpness":
            return F.adjust_sharpness(image, sharpness_factor=1.0 + magnitude)
        elif transform_id == "Posterize":
            return F.posterize(image, bits=int(magnitude))
        elif transform_id == "Solarize":
            bound = _FT._max_value(image.dtype) if isinstance(image, torch.Tensor) else 255.0
            return F.solarize(image, threshold=bound * magnitude)
        elif transform_id == "AutoContrast":
            return F.autocontrast(image)
        elif transform_id == "Equalize":
            return F.equalize(image)
        elif transform_id == "Invert":
            return F.invert(image)
        else:
            raise ValueError(f"No transform available for {transform_id}")


class AutoAugment(_AutoAugmentBase):
    _v1_transform_cls = _transforms.AutoAugment

    _AUGMENTATION_SPACE = {
        "ShearX": (lambda num_bins, height, width: torch.linspace(0.0, 0.3, num_bins), True),
        "ShearY": (lambda num_bins, height, width: torch.linspace(0.0, 0.3, num_bins), True),
        "TranslateX": (
            lambda num_bins, height, width: torch.linspace(0.0, 150.0 / 331.0 * width, num_bins),
            True,
        ),
        "TranslateY": (
            lambda num_bins, height, width: torch.linspace(0.0, 150.0 / 331.0 * height, num_bins),
            True,
        ),
        "Rotate": (lambda num_bins, height, width: torch.linspace(0.0, 30.0, num_bins), True),
        "Brightness": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Color": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Contrast": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Sharpness": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Posterize": (
            lambda num_bins, height, width: (8 - (torch.arange(num_bins) / ((num_bins - 1) / 4))).round().int(),
            False,
        ),
        "Solarize": (lambda num_bins, height, width: torch.linspace(1.0, 0.0, num_bins), False),
        "AutoContrast": (lambda num_bins, height, width: None, False),
        "Equalize": (lambda num_bins, height, width: None, False),
        "Invert": (lambda num_bins, height, width: None, False),
    }

    def __init__(
        self,
        policy: AutoAugmentPolicy = AutoAugmentPolicy.IMAGENET,
        interpolation: Union[InterpolationMode, int] = InterpolationMode.NEAREST,
        fill: Union[datapoints._FillType, Dict[Type, datapoints._FillType]] = None,
    ) -> None:
        super().__init__(interpolation=interpolation, fill=fill)
        self.policy = policy
        self._policies = self._get_policies(policy)

    def _get_policies(
        self, policy: AutoAugmentPolicy
    ) -> List[Tuple[Tuple[str, float, Optional[int]], Tuple[str, float, Optional[int]]]]:
        if policy == AutoAugmentPolicy.IMAGENET:
            return [
                (("Posterize", 0.4, 8), ("Rotate", 0.6, 9)),
                (("Solarize", 0.6, 5), ("AutoContrast", 0.6, None)),
                (("Equalize", 0.8, None), ("Equalize", 0.6, None)),
                (("Posterize", 0.6, 7), ("Posterize", 0.6, 6)),
                (("Equalize", 0.4, None), ("Solarize", 0.2, 4)),
                (("Equalize", 0.4, None), ("Rotate", 0.8, 8)),
                (("Solarize", 0.6, 3), ("Equalize", 0.6, None)),
                (("Posterize", 0.8, 5), ("Equalize", 1.0, None)),
                (("Rotate", 0.2, 3), ("Solarize", 0.6, 8)),
                (("Equalize", 0.6, None), ("Posterize", 0.4, 6)),
                (("Rotate", 0.8, 8), ("Color", 0.4, 0)),
                (("Rotate", 0.4, 9), ("Equalize", 0.6, None)),
                (("Equalize", 0.0, None), ("Equalize", 0.8, None)),
                (("Invert", 0.6, None), ("Equalize", 1.0, None)),
                (("Color", 0.6, 4), ("Contrast", 1.0, 8)),
                (("Rotate", 0.8, 8), ("Color", 1.0, 2)),
                (("Color", 0.8, 8), ("Solarize", 0.8, 7)),
                (("Sharpness", 0.4, 7), ("Invert", 0.6, None)),
                (("ShearX", 0.6, 5), ("Equalize", 1.0, None)),
                (("Color", 0.4, 0), ("Equalize", 0.6, None)),
                (("Equalize", 0.4, None), ("Solarize", 0.2, 4)),
                (("Solarize", 0.6, 5), ("AutoContrast", 0.6, None)),
                (("Invert", 0.6, None), ("Equalize", 1.0, None)),
                (("Color", 0.6, 4), ("Contrast", 1.0, 8)),
                (("Equalize", 0.8, None), ("Equalize", 0.6, None)),
            ]
        elif policy == AutoAugmentPolicy.CIFAR10:
            return [
                (("Invert", 0.1, None), ("Contrast", 0.2, 6)),
                (("Rotate", 0.7, 2), ("TranslateX", 0.3, 9)),
                (("Sharpness", 0.8, 1), ("Sharpness", 0.9, 3)),
                (("ShearY", 0.5, 8), ("TranslateY", 0.7, 9)),
                (("AutoContrast", 0.5, None), ("Equalize", 0.9, None)),
                (("ShearY", 0.2, 7), ("Posterize", 0.3, 7)),
                (("Color", 0.4, 3), ("Brightness", 0.6, 7)),
                (("Sharpness", 0.3, 9), ("Brightness", 0.7, 9)),
                (("Equalize", 0.6, None), ("Equalize", 0.5, None)),
                (("Contrast", 0.6, 7), ("Sharpness", 0.6, 5)),
                (("Color", 0.7, 7), ("TranslateX", 0.5, 8)),
                (("Equalize", 0.3, None), ("AutoContrast", 0.4, None)),
                (("TranslateY", 0.4, 3), ("Sharpness", 0.2, 6)),
                (("Brightness", 0.9, 6), ("Color", 0.2, 8)),
                (("Solarize", 0.5, 2), ("Invert", 0.0, None)),
                (("Equalize", 0.2, None), ("AutoContrast", 0.6, None)),
                (("Equalize", 0.2, None), ("Equalize", 0.6, None)),
                (("Color", 0.9, 9), ("Equalize", 0.6, None)),
                (("AutoContrast", 0.8, None), ("Solarize", 0.2, 8)),
                (("Brightness", 0.1, 3), ("Color", 0.7, 0)),
                (("Solarize", 0.4, 5), ("AutoContrast", 0.9, None)),
                (("TranslateY", 0.9, 9), ("TranslateY", 0.7, 9)),
                (("AutoContrast", 0.9, None), ("Solarize", 0.8, 3)),
                (("Equalize", 0.8, None), ("Invert", 0.1, None)),
                (("TranslateY", 0.7, 9), ("AutoContrast", 0.9, None)),
            ]
        elif policy == AutoAugmentPolicy.SVHN:
            return [
                (("ShearX", 0.9, 4), ("Invert", 0.2, None)),
                (("ShearY", 0.9, 8), ("Invert", 0.7, None)),
                (("Equalize", 0.6, None), ("Solarize", 0.6, 6)),
                (("Invert", 0.9, None), ("Equalize", 0.6, None)),
                (("Equalize", 0.6, None), ("Rotate", 0.9, 3)),
                (("ShearX", 0.9, 4), ("AutoContrast", 0.8, None)),
                (("ShearY", 0.9, 8), ("Invert", 0.4, None)),
                (("ShearY", 0.9, 5), ("Solarize", 0.2, 6)),
                (("Invert", 0.9, None), ("AutoContrast", 0.8, None)),
                (("Equalize", 0.6, None), ("Rotate", 0.9, 3)),
                (("ShearX", 0.9, 4), ("Solarize", 0.3, 3)),
                (("ShearY", 0.8, 8), ("Invert", 0.7, None)),
                (("Equalize", 0.9, None), ("TranslateY", 0.6, 6)),
                (("Invert", 0.9, None), ("Equalize", 0.6, None)),
                (("Contrast", 0.3, 3), ("Rotate", 0.8, 4)),
                (("Invert", 0.8, None), ("TranslateY", 0.0, 2)),
                (("ShearY", 0.7, 6), ("Solarize", 0.4, 8)),
                (("Invert", 0.6, None), ("Rotate", 0.8, 4)),
                (("ShearY", 0.3, 7), ("TranslateX", 0.9, 3)),
                (("ShearX", 0.1, 6), ("Invert", 0.6, None)),
                (("Solarize", 0.7, 2), ("TranslateY", 0.6, 7)),
                (("ShearY", 0.8, 4), ("Invert", 0.8, None)),
                (("ShearX", 0.7, 9), ("TranslateY", 0.8, 3)),
                (("ShearY", 0.8, 5), ("AutoContrast", 0.7, None)),
                (("ShearX", 0.7, 2), ("Invert", 0.1, None)),
            ]
        else:
            raise ValueError(f"The provided policy {policy} is not recognized.")

    def forward(self, *inputs: Any) -> Any:
        flat_inputs_with_spec, image_or_video = self._flatten_and_extract_image_or_video(inputs)
        height, width = get_spatial_size(image_or_video)

        policy = self._policies[int(torch.randint(len(self._policies), ()))]

        for transform_id, probability, magnitude_idx in policy:
            if not torch.rand(()) <= probability:
                continue

            magnitudes_fn, signed = self._AUGMENTATION_SPACE[transform_id]

            magnitudes = magnitudes_fn(10, height, width)
            if magnitudes is not None:
                magnitude = float(magnitudes[magnitude_idx])
                if signed and torch.rand(()) <= 0.5:
                    magnitude *= -1
            else:
                magnitude = 0.0

            image_or_video = self._apply_image_or_video_transform(
                image_or_video, transform_id, magnitude, interpolation=self.interpolation, fill=self.fill
            )

        return self._unflatten_and_insert_image_or_video(flat_inputs_with_spec, image_or_video)


class RandAugment(_AutoAugmentBase):
    _v1_transform_cls = _transforms.RandAugment
    _AUGMENTATION_SPACE = {
        "Identity": (lambda num_bins, height, width: None, False),
        "ShearX": (lambda num_bins, height, width: torch.linspace(0.0, 0.3, num_bins), True),
        "ShearY": (lambda num_bins, height, width: torch.linspace(0.0, 0.3, num_bins), True),
        "TranslateX": (
            lambda num_bins, height, width: torch.linspace(0.0, 150.0 / 331.0 * width, num_bins),
            True,
        ),
        "TranslateY": (
            lambda num_bins, height, width: torch.linspace(0.0, 150.0 / 331.0 * height, num_bins),
            True,
        ),
        "Rotate": (lambda num_bins, height, width: torch.linspace(0.0, 30.0, num_bins), True),
        "Brightness": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Color": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Contrast": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Sharpness": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Posterize": (
            lambda num_bins, height, width: (8 - (torch.arange(num_bins) / ((num_bins - 1) / 4))).round().int(),
            False,
        ),
        "Solarize": (lambda num_bins, height, width: torch.linspace(1.0, 0.0, num_bins), False),
        "AutoContrast": (lambda num_bins, height, width: None, False),
        "Equalize": (lambda num_bins, height, width: None, False),
    }

    def __init__(
        self,
        num_ops: int = 2,
        magnitude: int = 9,
        num_magnitude_bins: int = 31,
        interpolation: Union[InterpolationMode, int] = InterpolationMode.NEAREST,
        fill: Union[datapoints._FillType, Dict[Type, datapoints._FillType]] = None,
    ) -> None:
        super().__init__(interpolation=interpolation, fill=fill)
        self.num_ops = num_ops
        self.magnitude = magnitude
        self.num_magnitude_bins = num_magnitude_bins

    def forward(self, *inputs: Any) -> Any:
        flat_inputs_with_spec, image_or_video = self._flatten_and_extract_image_or_video(inputs)
        height, width = get_spatial_size(image_or_video)

        for _ in range(self.num_ops):
            transform_id, (magnitudes_fn, signed) = self._get_random_item(self._AUGMENTATION_SPACE)
            magnitudes = magnitudes_fn(self.num_magnitude_bins, height, width)
            if magnitudes is not None:
                magnitude = float(magnitudes[self.magnitude])
                if signed and torch.rand(()) <= 0.5:
                    magnitude *= -1
            else:
                magnitude = 0.0
            image_or_video = self._apply_image_or_video_transform(
                image_or_video, transform_id, magnitude, interpolation=self.interpolation, fill=self.fill
            )

        return self._unflatten_and_insert_image_or_video(flat_inputs_with_spec, image_or_video)


class TrivialAugmentWide(_AutoAugmentBase):
    _v1_transform_cls = _transforms.TrivialAugmentWide
    _AUGMENTATION_SPACE = {
        "Identity": (lambda num_bins, height, width: None, False),
        "ShearX": (lambda num_bins, height, width: torch.linspace(0.0, 0.99, num_bins), True),
        "ShearY": (lambda num_bins, height, width: torch.linspace(0.0, 0.99, num_bins), True),
        "TranslateX": (lambda num_bins, height, width: torch.linspace(0.0, 32.0, num_bins), True),
        "TranslateY": (lambda num_bins, height, width: torch.linspace(0.0, 32.0, num_bins), True),
        "Rotate": (lambda num_bins, height, width: torch.linspace(0.0, 135.0, num_bins), True),
        "Brightness": (lambda num_bins, height, width: torch.linspace(0.0, 0.99, num_bins), True),
        "Color": (lambda num_bins, height, width: torch.linspace(0.0, 0.99, num_bins), True),
        "Contrast": (lambda num_bins, height, width: torch.linspace(0.0, 0.99, num_bins), True),
        "Sharpness": (lambda num_bins, height, width: torch.linspace(0.0, 0.99, num_bins), True),
        "Posterize": (
            lambda num_bins, height, width: (8 - (torch.arange(num_bins) / ((num_bins - 1) / 6))).round().int(),
            False,
        ),
        "Solarize": (lambda num_bins, height, width: torch.linspace(1.0, 0.0, num_bins), False),
        "AutoContrast": (lambda num_bins, height, width: None, False),
        "Equalize": (lambda num_bins, height, width: None, False),
    }

    def __init__(
        self,
        num_magnitude_bins: int = 31,
        interpolation: Union[InterpolationMode, int] = InterpolationMode.NEAREST,
        fill: Union[datapoints._FillType, Dict[Type, datapoints._FillType]] = None,
    ):
        super().__init__(interpolation=interpolation, fill=fill)
        self.num_magnitude_bins = num_magnitude_bins

    def forward(self, *inputs: Any) -> Any:
        flat_inputs_with_spec, image_or_video = self._flatten_and_extract_image_or_video(inputs)
        height, width = get_spatial_size(image_or_video)

        transform_id, (magnitudes_fn, signed) = self._get_random_item(self._AUGMENTATION_SPACE)

        magnitudes = magnitudes_fn(self.num_magnitude_bins, height, width)
        if magnitudes is not None:
            magnitude = float(magnitudes[int(torch.randint(self.num_magnitude_bins, ()))])
            if signed and torch.rand(()) <= 0.5:
                magnitude *= -1
        else:
            magnitude = 0.0

        image_or_video = self._apply_image_or_video_transform(
            image_or_video, transform_id, magnitude, interpolation=self.interpolation, fill=self.fill
        )
        return self._unflatten_and_insert_image_or_video(flat_inputs_with_spec, image_or_video)


class AugMix(_AutoAugmentBase):
    _v1_transform_cls = _transforms.AugMix

    _PARTIAL_AUGMENTATION_SPACE = {
        "ShearX": (lambda num_bins, height, width: torch.linspace(0.0, 0.3, num_bins), True),
        "ShearY": (lambda num_bins, height, width: torch.linspace(0.0, 0.3, num_bins), True),
        "TranslateX": (lambda num_bins, height, width: torch.linspace(0.0, width / 3.0, num_bins), True),
        "TranslateY": (lambda num_bins, height, width: torch.linspace(0.0, height / 3.0, num_bins), True),
        "Rotate": (lambda num_bins, height, width: torch.linspace(0.0, 30.0, num_bins), True),
        "Posterize": (
            lambda num_bins, height, width: (4 - (torch.arange(num_bins) / ((num_bins - 1) / 4))).round().int(),
            False,
        ),
        "Solarize": (lambda num_bins, height, width: torch.linspace(1.0, 0.0, num_bins), False),
        "AutoContrast": (lambda num_bins, height, width: None, False),
        "Equalize": (lambda num_bins, height, width: None, False),
    }
    _AUGMENTATION_SPACE: Dict[str, Tuple[Callable[[int, int, int], Optional[torch.Tensor]], bool]] = {
        **_PARTIAL_AUGMENTATION_SPACE,
        "Brightness": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Color": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Contrast": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
        "Sharpness": (lambda num_bins, height, width: torch.linspace(0.0, 0.9, num_bins), True),
    }

    def __init__(
        self,
        severity: int = 3,
        mixture_width: int = 3,
        chain_depth: int = -1,
        alpha: float = 1.0,
        all_ops: bool = True,
        interpolation: Union[InterpolationMode, int] = InterpolationMode.BILINEAR,
        fill: Union[datapoints._FillType, Dict[Type, datapoints._FillType]] = None,
    ) -> None:
        super().__init__(interpolation=interpolation, fill=fill)
        self._PARAMETER_MAX = 10
        if not (1 <= severity <= self._PARAMETER_MAX):
            raise ValueError(f"The severity must be between [1, {self._PARAMETER_MAX}]. Got {severity} instead.")
        self.severity = severity
        self.mixture_width = mixture_width
        self.chain_depth = chain_depth
        self.alpha = alpha
        self.all_ops = all_ops

    def _sample_dirichlet(self, params: torch.Tensor) -> torch.Tensor:
        # Must be on a separate method so that we can overwrite it in tests.
        return torch._sample_dirichlet(params)

    def forward(self, *inputs: Any) -> Any:
        flat_inputs_with_spec, orig_image_or_video = self._flatten_and_extract_image_or_video(inputs)
        height, width = get_spatial_size(orig_image_or_video)

        if isinstance(orig_image_or_video, torch.Tensor):
            image_or_video = orig_image_or_video
        else:  # isinstance(inpt, PIL.Image.Image):
            image_or_video = F.pil_to_tensor(orig_image_or_video)

        augmentation_space = self._AUGMENTATION_SPACE if self.all_ops else self._PARTIAL_AUGMENTATION_SPACE

        orig_dims = list(image_or_video.shape)
        expected_ndim = 5 if isinstance(orig_image_or_video, datapoints.Video) else 4
        batch = image_or_video.reshape([1] * max(expected_ndim - image_or_video.ndim, 0) + orig_dims)
        batch_dims = [batch.size(0)] + [1] * (batch.ndim - 1)

        # Sample the beta weights for combining the original and augmented image or video. To get Beta, we use a
        # Dirichlet with 2 parameters. The 1st column stores the weights of the original and the 2nd the ones of
        # augmented image or video.
        m = self._sample_dirichlet(
            torch.tensor([self.alpha, self.alpha], device=batch.device).expand(batch_dims[0], -1)
        )

        # Sample the mixing weights and combine them with the ones sampled from Beta for the augmented images or videos.
        combined_weights = self._sample_dirichlet(
            torch.tensor([self.alpha] * self.mixture_width, device=batch.device).expand(batch_dims[0], -1)
        ) * m[:, 1].reshape([batch_dims[0], -1])

        mix = m[:, 0].reshape(batch_dims) * batch
        for i in range(self.mixture_width):
            aug = batch
            depth = self.chain_depth if self.chain_depth > 0 else int(torch.randint(low=1, high=4, size=(1,)).item())
            for _ in range(depth):
                transform_id, (magnitudes_fn, signed) = self._get_random_item(augmentation_space)

                magnitudes = magnitudes_fn(self._PARAMETER_MAX, height, width)
                if magnitudes is not None:
                    magnitude = float(magnitudes[int(torch.randint(self.severity, ()))])
                    if signed and torch.rand(()) <= 0.5:
                        magnitude *= -1
                else:
                    magnitude = 0.0

                aug = self._apply_image_or_video_transform(
                    aug, transform_id, magnitude, interpolation=self.interpolation, fill=self.fill
                )
            mix.add_(combined_weights[:, i].reshape(batch_dims) * aug)
        mix = mix.reshape(orig_dims).to(dtype=image_or_video.dtype)

        if isinstance(orig_image_or_video, (datapoints.Image, datapoints.Video)):
            mix = orig_image_or_video.wrap_like(orig_image_or_video, mix)  # type: ignore[arg-type]
        elif isinstance(orig_image_or_video, PIL.Image.Image):
            mix = F.to_image_pil(mix)

        return self._unflatten_and_insert_image_or_video(flat_inputs_with_spec, mix)
