import csv
import os
import os.path
from typing import Any, Callable, Optional, Tuple

import torch
from PIL import Image

from .utils import verify_str_arg, check_integrity
from .vision import VisionDataset


class FER2013(VisionDataset):
    """`FER2013
    <https://www.kaggle.com/c/challenges-in-representation-learning-facial-expression-recognition-challenge>`_ Dataset.

    Args:
        root (string): Root directory of dataset where directory
            ``caltech101`` exists or will be saved to if download is set to True.
        split (string, optional): The dataset split, supports ``"train"`` (default), or ``"test"``.
        transform (callable, optional): A function/transform that takes in an PIL image and returns a transformed
            version. E.g, ``transforms.RandomCrop``
        target_transform (callable, optional): A function/transform that takes in the target and transforms it.
    """

    _RESOURCES = {
        "train": ("train.csv", "3f0dfb3d3fd99c811a1299cb947e3131"),
        "test": ("test.csv", "b02c2298636a634e8c2faabbf3ea9a23"),
    }

    def __init__(
        self,
        root: str,
        split: str = "train",
        transform: Optional[Callable] = None,
        target_transform: Optional[Callable] = None,
    ) -> None:
        self._split = verify_str_arg(split, "split", self._RESOURCES.keys())
        super().__init__(root, transform=transform, target_transform=target_transform)

        with open(self._verify_integrity(), "r", newline="") as file:
            self._samples = [
                (
                    torch.tensor([int(idx) for idx in row["pixels"].split()], dtype=torch.uint8).reshape(48, 48),
                    int(row["emotion"]) if "emotion" in row else None,
                )
                for row in csv.DictReader(file)
            ]

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int) -> Tuple[Any, Any]:
        image_tensor, target = self._samples[idx]
        image = Image.fromarray(image_tensor.numpy())

        if self.transform is not None:
            image = self.transform(image)

        if self.target_transform is not None:
            target = self.target_transform(target)

        return image, target

    def _verify_integrity(self):
        base_folder = os.path.join(self.root, type(self).__name__.lower())
        file_name, md5 = self._RESOURCES[self._split]
        file = os.path.join(base_folder, file_name)
        if not check_integrity(file, md5=md5):
            raise RuntimeError(
                f"{file_name} not found in {base_folder} or corrupted. "
                f"You can download it from "
                f"https://www.kaggle.com/c/challenges-in-representation-learning-facial-expression-recognition-challenge"
            )
        return file

    def extra_repr(self) -> str:
        return f"split={self._split}"
