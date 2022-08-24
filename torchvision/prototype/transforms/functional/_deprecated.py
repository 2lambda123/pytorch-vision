import warnings
from typing import Any

import PIL.Image
import torch

from torchvision.prototype import features
from torchvision.transforms import functional as _F


# TODO: this was copied from `torchvision.prototype.transforms._utils`. Given that this is not related to pytree / the
#  Transform object, we should probably move it to somewhere else. One possibility is `functional._utils` so both the
#  functionals and the transforms have access to it. We could also move it `features` since it literally checks for the
#  _Feature type.
def is_simple_tensor(inpt: Any) -> bool:
    return isinstance(inpt, torch.Tensor) and not isinstance(inpt, features._Feature)


def to_grayscale(inpt: PIL.Image.Image, num_output_channels: int = 1) -> PIL.Image.Image:
    call = ", num_output_channels=3" if num_output_channels == 3 else ""
    replacement = "convert_color_space(..., color_space=features.ColorSpace.GRAY)"
    if num_output_channels == 3:
        replacement = f"convert_color_space({replacement}, color_space=features.ColorSpace.RGB)"
    warnings.warn(
        f"The function `to_grayscale(...{call})` is deprecated in will be removed in a future release. "
        f"Instead, please use `{replacement}`.",
    )

    return _F.to_grayscale(inpt, num_output_channels=num_output_channels)


def rgb_to_grayscale(inpt: Any, num_output_channels: int = 1) -> Any:
    if num_output_channels not in (1, 3):
        raise ValueError("num_output_channels should be either 1 or 3")

    old_color_space = features.Image.guess_color_space(inpt) if is_simple_tensor(inpt) else None

    call = ", num_output_channels=3" if num_output_channels == 3 else ""
    replacement = (
        f"convert_color_space(..., color_space=features.ColorSpace.GRAY"
        f"{f', old_color_space=features.ColorSpace.{old_color_space}' if old_color_space is not None else ''})"
    )
    if num_output_channels == 3:
        replacement = (
            f"convert_color_space({replacement}, color_space=features.ColorSpace.RGB"
            f"{f', old_color_space=features.ColorSpace.GRAY' if old_color_space is not None else ''})"
        )
    warnings.warn(
        f"The function `rgb_to_grayscale(...{call})` is deprecated in will be removed in a future release. "
        f"Instead, please use `{replacement}`.",
    )

    return _F.rgb_to_grayscale(inpt, num_output_channels=num_output_channels)
