from typing import Union

import numpy as np
import PIL.Image
import torch
from torchvision import datapoints
from torchvision.transforms import functional as _F


@torch.jit.unused
def to_image(inpt: Union[torch.Tensor, PIL.Image.Image, np.ndarray]) -> datapoints.Image:
    if isinstance(inpt, np.ndarray):
        output = torch.from_numpy(inpt).permute((2, 0, 1)).contiguous()
    elif isinstance(inpt, PIL.Image.Image):
        output = pil_to_tensor(inpt)
    elif isinstance(inpt, torch.Tensor):
        output = inpt
    else:
        raise TypeError(f"Input can either be a numpy array or a PIL image, but got {type(inpt)} instead.")
    return datapoints.Image(output)


to_pil_image = _F.to_pil_image
pil_to_tensor = _F.pil_to_tensor
