import dataclasses

from collections import defaultdict
from typing import Callable, Dict, List, Sequence, Type

import pytest
import torchvision.prototype.transforms.functional as F
from prototype_transforms_kernel_infos import KERNEL_INFOS, TestMark
from torchvision.prototype import features

__all__ = ["DispatcherInfo", "DISPATCHER_INFOS"]

KERNEL_SAMPLE_INPUTS_FN_MAP = {info.kernel: info.sample_inputs_fn for info in KERNEL_INFOS}


@dataclasses.dataclass
class DispatcherInfo:
    dispatcher: Callable
    kernels: Dict[Type, Callable]
    skips: Sequence = dataclasses.field(default_factory=list)
    _skips_map: Dict = dataclasses.field(default=None, init=False)

    test_marks: Sequence[TestMark] = dataclasses.field(default_factory=list)
    _test_marks_map: Dict[str, List[TestMark]] = dataclasses.field(default=None, init=False)

    def __post_init__(self):
        test_marks_map = defaultdict(list)
        for test_mark in self.test_marks:
            test_marks_map[test_mark.test_id].append(test_mark)
        self._test_marks_map = dict(test_marks_map)

    def get_marks(self, test_id, args_kwargs):
        return [
            conditional_mark.mark
            for conditional_mark in self._test_marks_map.get(test_id, [])
            if conditional_mark.condition(args_kwargs)
        ]

    def sample_inputs(self, *types):
        for type in types or self.kernels.keys():
            if type not in self.kernels:
                raise pytest.UsageError(f"There is no kernel registered for type {type.__name__}")

            yield from KERNEL_SAMPLE_INPUTS_FN_MAP[self.kernels[type]]()


DISPATCHER_INFOS = [
    DispatcherInfo(
        F.horizontal_flip,
        kernels={
            features.Image: F.horizontal_flip_image_tensor,
            features.BoundingBox: F.horizontal_flip_bounding_box,
            features.Mask: F.horizontal_flip_mask,
        },
    ),
    DispatcherInfo(
        F.resize,
        kernels={
            features.Image: F.resize_image_tensor,
            features.BoundingBox: F.resize_bounding_box,
            features.Mask: F.resize_mask,
        },
    ),
    DispatcherInfo(
        F.affine,
        kernels={
            features.Image: F.affine_image_tensor,
            features.BoundingBox: F.affine_bounding_box,
            features.Mask: F.affine_mask,
        },
    ),
    DispatcherInfo(
        F.vertical_flip,
        kernels={
            features.Image: F.vertical_flip_image_tensor,
            features.BoundingBox: F.vertical_flip_bounding_box,
            features.Mask: F.vertical_flip_mask,
        },
    ),
    DispatcherInfo(
        F.rotate,
        kernels={
            features.Image: F.rotate_image_tensor,
            features.BoundingBox: F.rotate_bounding_box,
            features.Mask: F.rotate_mask,
        },
    ),
    DispatcherInfo(
        F.crop,
        kernels={
            features.Image: F.crop_image_tensor,
            features.BoundingBox: F.crop_bounding_box,
            features.Mask: F.crop_mask,
        },
    ),
    DispatcherInfo(
        F.resized_crop,
        kernels={
            features.Image: F.resized_crop_image_tensor,
            features.BoundingBox: F.resized_crop_bounding_box,
            features.Mask: F.resized_crop_mask,
        },
    ),
    DispatcherInfo(
        F.pad,
        kernels={
            features.Image: F.pad_image_tensor,
            features.BoundingBox: F.pad_bounding_box,
            features.Mask: F.pad_mask,
        },
    ),
    DispatcherInfo(
        F.perspective,
        kernels={
            features.Image: F.perspective_image_tensor,
            features.BoundingBox: F.perspective_bounding_box,
            features.Mask: F.perspective_mask,
        },
    ),
    DispatcherInfo(
        F.elastic,
        kernels={
            features.Image: F.elastic_image_tensor,
            features.BoundingBox: F.elastic_bounding_box,
            features.Mask: F.elastic_mask,
        },
    ),
    DispatcherInfo(
        F.center_crop,
        kernels={
            features.Image: F.center_crop_image_tensor,
            features.BoundingBox: F.center_crop_bounding_box,
            features.Mask: F.center_crop_mask,
        },
    ),
    DispatcherInfo(
        F.gaussian_blur,
        kernels={
            features.Image: F.gaussian_blur_image_tensor,
        },
    ),
    DispatcherInfo(
        F.equalize,
        kernels={
            features.Image: F.equalize_image_tensor,
        },
    ),
    DispatcherInfo(
        F.invert,
        kernels={
            features.Image: F.invert_image_tensor,
        },
    ),
    DispatcherInfo(
        F.posterize,
        kernels={
            features.Image: F.posterize_image_tensor,
        },
    ),
    DispatcherInfo(
        F.solarize,
        kernels={
            features.Image: F.solarize_image_tensor,
        },
    ),
    DispatcherInfo(
        F.autocontrast,
        kernels={
            features.Image: F.autocontrast_image_tensor,
        },
    ),
    DispatcherInfo(
        F.adjust_sharpness,
        kernels={
            features.Image: F.adjust_sharpness_image_tensor,
        },
    ),
    DispatcherInfo(
        F.erase,
        kernels={
            features.Image: F.erase_image_tensor,
        },
    ),
    DispatcherInfo(
        F.adjust_brightness,
        kernels={
            features.Image: F.adjust_brightness_image_tensor,
        },
    ),
    DispatcherInfo(
        F.adjust_contrast,
        kernels={
            features.Image: F.adjust_contrast_image_tensor,
        },
    ),
    DispatcherInfo(
        F.adjust_gamma,
        kernels={
            features.Image: F.adjust_gamma_image_tensor,
        },
    ),
    DispatcherInfo(
        F.adjust_hue,
        kernels={
            features.Image: F.adjust_hue_image_tensor,
        },
    ),
    DispatcherInfo(
        F.adjust_saturation,
        kernels={
            features.Image: F.adjust_saturation_image_tensor,
        },
    ),
    DispatcherInfo(
        F.five_crop,
        kernels={
            features.Image: F.five_crop_image_tensor,
        },
        test_marks=[
            TestMark(
                ("TestDispatchers", "test_scripted_smoke"),
                pytest.mark.xfail(reason="Integer size is not supported when scripting five_crop."),
                condition=lambda args_kwargs: isinstance(args_kwargs.kwargs["size"], int),
            )
        ],
    ),
    DispatcherInfo(
        F.ten_crop,
        kernels={
            features.Image: F.ten_crop_image_tensor,
        },
        test_marks=[
            TestMark(
                ("TestDispatchers", "test_scripted_smoke"),
                pytest.mark.xfail(reason="Integer size is not supported when scripting ten_crop."),
                condition=lambda args_kwargs: isinstance(args_kwargs.kwargs["size"], int),
            )
        ],
    ),
    DispatcherInfo(
        F.normalize,
        kernels={
            features.Image: F.normalize_image_tensor,
        },
    ),
]
