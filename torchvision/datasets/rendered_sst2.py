from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import PIL.Image

from .folder import make_dataset
from .utils import download_and_extract_archive, verify_str_arg
from .vision import VisionDataset


class RenderedSST2(VisionDataset):
    """`The Rendered SST2 Dataset <https://github.com/openai/CLIP/blob/main/data/rendered-sst2.md>`_.

    Rendered SST2 is an image classification dataset used to evaluate the models capability on optical
    character recognition. This dataset was generated by rendering sentences in the Standford Sentiment
    Treebank v2 dataset.

    This dataset contains two classes (positive and negative) and is divided in three splits: a  train
    split containing 6920 images (3610 positive and 3310 negative), a validation split containing 872 images
    (444 positive and 428 negative), and a test split containing 1821 images (909 positive and 912 negative).

    Args:
        root (str or ``pathlib.Path``): Root directory of the dataset.
        split (string, optional): The dataset split, supports ``"train"`` (default), `"val"` and ``"test"``.
        transform (callable, optional): A function/transform that takes in a PIL image and returns a transformed
            version. E.g, ``transforms.RandomCrop``.
        target_transform (callable, optional): A function/transform that takes in the target and transforms it.
        download (bool, optional): If True, downloads the dataset from the internet and
            puts it in root directory. If dataset is already downloaded, it is not
            downloaded again. Default is False.
    """

    _URL = "https://openaipublic.azureedge.net/clip/data/rendered-sst2.tgz"
    _MD5 = "2384d08e9dcfa4bd55b324e610496ee5"

    def __init__(
        self,
        root: str,
        split: str = "train",
        transform: Optional[Callable] = None,
        target_transform: Optional[Callable] = None,
        download: bool = False,
    ) -> None:
        super().__init__(root, transform=transform, target_transform=target_transform)
        self._split = verify_str_arg(split, "split", ("train", "val", "test"))
        self._split_to_folder = {"train": "train", "val": "valid", "test": "test"}
        self._base_folder = Path(self.root) / "rendered-sst2"
        self.classes = ["negative", "positive"]
        self.class_to_idx = {"negative": 0, "positive": 1}

        if download:
            self._download()

        if not self._check_exists():
            raise RuntimeError("Dataset not found. You can use download=True to download it")

        self._samples = make_dataset(str(self._base_folder / self._split_to_folder[self._split]), extensions=("png",))

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int) -> Tuple[Any, Any]:
        image_file, label = self._samples[idx]
        image = PIL.Image.open(image_file).convert("RGB")

        if self.transform:
            image = self.transform(image)

        if self.target_transform:
            label = self.target_transform(label)

        return image, label

    def extra_repr(self) -> str:
        return f"split={self._split}"

    def _check_exists(self) -> bool:
        for class_label in set(self.classes):
            if not (self._base_folder / self._split_to_folder[self._split] / class_label).is_dir():
                return False
        return True

    def _download(self) -> None:
        if self._check_exists():
            return
        download_and_extract_archive(self._URL, download_root=self.root, md5=self._MD5)
