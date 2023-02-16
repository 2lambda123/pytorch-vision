from ._bounding_box import BoundingBox, BoundingBoxFormat
from ._datapoint import FillType, FillTypeJIT, InputType, InputTypeJIT
from ._image import Image, ImageType, ImageTypeJIT, TensorImageType, TensorImageTypeJIT
from ._mask import Mask
from ._video import TensorVideoType, TensorVideoTypeJIT, Video, VideoType, VideoTypeJIT

from ._dataset_wrapper import wrap_dataset_for_transforms_v2  # type: ignore[attr-defined]  # usort: skip

from torchvision import _WARN_ABOUT_BETA_TRANSFORMS

if _WARN_ABOUT_BETA_TRANSFORMS:
    import warnings

    warnings.warn("THE WARNING")
