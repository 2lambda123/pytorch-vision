from __future__ import annotations

import warnings
from typing import Any, Optional, Union, Tuple, cast

import torch
from torchvision._utils import StrEnum
from torchvision.transforms.functional import to_pil_image
from torchvision.utils import draw_bounding_boxes
from torchvision.utils import make_grid

from ._bounding_box import BoundingBox
from ._feature import _Feature


class ColorSpace(StrEnum):
    OTHER = StrEnum.auto()
    GRAY = StrEnum.auto()
    GRAY_ALPHA = StrEnum.auto()
    RGB = StrEnum.auto()
    RGB_ALPHA = StrEnum.auto()

    @classmethod
    def from_pil_mode(cls, mode: str) -> ColorSpace:
        if mode == "L":
            return cls.GRAY
        elif mode == "LA":
            return cls.GRAY_ALPHA
        elif mode == "RGB":
            return cls.RGB
        elif mode == "RGBA":
            return cls.RGB_ALPHA
        else:
            return cls.OTHER


class Image(_Feature):
    color_space: ColorSpace

    def __new__(
        cls,
        data: Any,
        *,
        color_space: Optional[Union[ColorSpace, str]] = None,
        dtype: Optional[torch.dtype] = None,
        device: Optional[Union[torch.device, str, int]] = None,
        requires_grad: bool = False,
    ) -> Image:
        data = torch.as_tensor(data, dtype=dtype, device=device)  # type: ignore[arg-type]
        if data.ndim < 2:
            raise ValueError
        elif data.ndim == 2:
            data = data.unsqueeze(0)
        image = super().__new__(cls, data, requires_grad=requires_grad)

        if color_space is None:
            color_space = cls.guess_color_space(image)
            if color_space == ColorSpace.OTHER:
                warnings.warn("Unable to guess a specific color space. Consider passing it explicitly.")
        elif isinstance(color_space, str):
            color_space = ColorSpace.from_str(color_space.upper())
        elif not isinstance(color_space, ColorSpace):
            raise ValueError
        image.color_space = color_space

        return image

    @classmethod
    def new_like(
        cls, other: Image, data: Any, *, color_space: Optional[Union[ColorSpace, str]] = None, **kwargs: Any
    ) -> Image:
        return super().new_like(
            other, data, color_space=color_space if color_space is not None else other.color_space, **kwargs
        )

    @property
    def image_size(self) -> Tuple[int, int]:
        return cast(Tuple[int, int], self.shape[-2:])

    @property
    def num_channels(self) -> int:
        return self.shape[-3]

    @staticmethod
    def guess_color_space(data: torch.Tensor) -> ColorSpace:
        if data.ndim < 2:
            return ColorSpace.OTHER
        elif data.ndim == 2:
            return ColorSpace.GRAY

        num_channels = data.shape[-3]
        if num_channels == 1:
            return ColorSpace.GRAY
        elif num_channels == 2:
            return ColorSpace.GRAY_ALPHA
        elif num_channels == 3:
            return ColorSpace.RGB
        elif num_channels == 4:
            return ColorSpace.RGB_ALPHA
        else:
            return ColorSpace.OTHER

    def show(self) -> None:
        # TODO: this is useful for developing and debugging but we should remove or at least revisit this before we
        #  promote this out of the prototype state
        to_pil_image(make_grid(self.view(-1, *self.shape[-3:]))).show()

    def draw_bounding_box(self, bounding_box: BoundingBox, **kwargs: Any) -> Image:
        # TODO: this is useful for developing and debugging but we should remove or at least revisit this before we
        #  promote this out of the prototype state
        return Image.new_like(self, draw_bounding_boxes(self, bounding_box.to_format("xyxy").view(-1, 4), **kwargs))

    def horizontal_flip(self) -> Image:
        output = self._F.horizontal_flip_image_tensor(self)
        return Image.new_like(self, output)

    def vertical_flip(self) -> Image:
        output = self._F.vertical_flip_image_tensor(self)
        return Image.new_like(self, output)

    def resize(self, size, *, interpolation, max_size, antialias) -> Image:
        output = self._F.resize_image_tensor(
            self, size, interpolation=interpolation, max_size=max_size, antialias=antialias
        )
        return Image.new_like(self, output)

    def crop(self, top: int, left: int, height: int, width: int) -> Image:
        output = self._F.crop_image_tensor(self, top, left, height, width)
        return Image.new_like(self, output)

    def center_crop(self, output_size) -> Image:
        output = self._F.center_crop_image_tensor(self, output_size=output_size)
        return Image.new_like(self, output)

    def resized_crop(self, top, left, height, width, *, size, interpolation, antialias) -> Image:
        output = self._F.resized_crop_image_tensor(
            self, top, left, height, width, size=list(size), interpolation=interpolation, antialias=antialias
        )
        return Image.new_like(self, output)

    def pad(self, padding, *, fill, padding_mode) -> Image:
        # Previous message from previous implementation:
        # PyTorch's pad supports only integers on fill. So we need to overwrite the colour
        # vfdev-5: pytorch pad support both int and floats but keeps original dtyp
        # if user pads int image with float pad, they need to cast the image first to float
        # before padding. Let's remove previous manual float fill support.
        output = self._F.pad_image_tensor(self, padding, fill=fill, padding_mode=padding_mode)
        return Image.new_like(self, output)

    def rotate(self, angle, *, interpolation, expand, fill, center) -> Image:
        output = self._F.rotate_image_tensor(
            self, angle, interpolation=interpolation, expand=expand, fill=fill, center=center
        )
        return Image.new_like(self, output)

    def affine(self, angle, *, translate, scale, shear, interpolation, fill, center) -> Image:
        output = self._F.affine_image_tensor(
            self,
            angle,
            translate=translate,
            scale=scale,
            shear=shear,
            interpolation=interpolation,
            fill=fill,
            center=center,
        )
        return Image.new_like(self, output)

    def perspective(self, perspective_coeffs, *, interpolation, fill) -> Image:
        output = self._F.perspective_image_tensor(self, perspective_coeffs, interpolation=interpolation, fill=fill)
        return Image.new_like(self, output)

    def adjust_brightness(self, brightness_factor: float) -> Image:
        output = self._F.adjust_brightness_image_tensor(self, brightness_factor=brightness_factor)
        return Image.new_like(self, output)

    def adjust_saturation(self, saturation_factor: float) -> Image:
        output = self._F.adjust_saturation_image_tensor(self, saturation_factor=saturation_factor)
        return Image.new_like(self, output)

    def adjust_contrast(self, contrast_factor: float) -> Image:
        output = self._F.adjust_contrast_image_tensor(self, contrast_factor=contrast_factor)
        return Image.new_like(self, output)

    def adjust_sharpness(self, sharpness_factor: float) -> Image:
        output = self._F.adjust_sharpness_image_tensor(self, sharpness_factor=sharpness_factor)
        return Image.new_like(self, output)

    def adjust_hue(self, hue_factor: float) -> Image:
        output = self._F.adjust_hue_image_tensor(self, hue_factor=hue_factor)
        return Image.new_like(self, output)

    def adjust_gamma(self, gamma: float, gain: float = 1) -> Image:
        output = self._F.adjust_gamma_image_tensor(self, gamma=gamma, gain=gain)
        return Image.new_like(self, output)

    def posterize(self, bits: int) -> Image:
        output = self._F.posterize_image_tensor(self, bits=bits)
        return Image.new_like(self, output)

    def solarize(self, threshold: float) -> Image:
        output = self._F.solarize_image_tensor(self, threshold=threshold)
        return Image.new_like(self, output)

    def autocontrast(self) -> Image:
        output = self._F.autocontrast_image_tensor(self)
        return Image.new_like(self, output)

    def equalize(self) -> Image:
        output = self._F.equalize_image_tensor(self)
        return Image.new_like(self, output)

    def invert(self) -> Image:
        output = self._F.invert_image_tensor(self)
        return Image.new_like(self, output)

    def erase(self, i, j, h, w, v) -> Image:
        output = self._F.erase_image_tensor(self, i, j, h, w, v)
        return Image.new_like(self, output)

    def mixup(self, lam: float) -> Image:
        if self.ndim < 4:
            raise ValueError("Need a batch of images")
        output = self.clone()
        output = output.roll(1, -4).mul_(1 - lam).add_(output.mul_(lam))
        return Image.new_like(self, output)

    def cutmix(self, *, box: Tuple[int, int, int, int], lam_adjusted: float) -> Image:
        lam_adjusted  # unused
        if self.ndim < 4:
            raise ValueError("Need a batch of images")
        x1, y1, x2, y2 = box
        image_rolled = self.roll(1, -4)
        output = self.clone()
        output[..., y1:y2, x1:x2] = image_rolled[..., y1:y2, x1:x2]
        return Image.new_like(self, output)
