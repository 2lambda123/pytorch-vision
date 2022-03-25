import itertools
import pathlib
from typing import Any, Dict, List, Optional, Tuple, BinaryIO, Iterator

from torchdata.datapipes.iter import IterDataPipe, Mapper, Filter, IterKeyZipper, LineReader
from torchvision.prototype.datasets.utils import (
    Dataset,
    DatasetConfig,
    DatasetInfo,
    HttpResource,
    OnlineResource,
    GDriveResource,
)
from torchvision.prototype.datasets.utils._internal import (
    INFINITE_BUFFER_SIZE,
    hint_sharding,
    hint_shuffling,
    path_accessor,
    path_comparator,
    getitem,
)
from torchvision.prototype.features import BoundingBox, EncodedImage, Label


class WIDERFaceAnnotationParser(IterDataPipe[Tuple[str, List[Dict[str, str]]]]):
    def __init__(self, datapipe: IterDataPipe[str]) -> None:
        self.datapipe = datapipe

    def __iter__(self) -> Iterator[Tuple[str, List[Dict[str, str]]]]:
        lines = iter(self.datapipe)
        for line in lines:
            name = line.rsplit("/")[1]
            anns = [
                dict(
                    zip(
                        ("x", "y", "w", "h", "blur", "expression", "illumination", "invalid", "occlusion", "pose"),
                        next(lines).split(" "),
                    )
                )
                for _ in range(int(next(lines)))
            ]
            yield name, anns


class WIDERFace(Dataset):
    def _make_info(self) -> DatasetInfo:
        return DatasetInfo(
            "widerface",
            homepage="http://shuoyang1213.me/WIDERFACE/",
            valid_options=dict(split=("train", "val", "test")),
        )

    def resources(self, config: DatasetConfig) -> List[OnlineResource]:
        id, sha256 = {
            "train": (
                "15hGDLhsx8bLgLcIRD5DhYt5iBxnjNF1M",
                "e23b76129c825cafae8be944f65310b2e1ba1c76885afe732f179c41e5ed6d59",
            ),
            "val": (
                "1GUCogbp16PMGa39thoMMeWxp7Rp5oM8Q",
                "f9efbd09f28c5d2d884be8c0eaef3967158c866a593fc36ab0413e4b2a58a17a",
            ),
            "test": (
                "1HIfDbVEWKmsYKJZm4lchTBDLW5N7dY5T",
                "3b0313e11ea292ec58894b47ac4c0503b230e12540330845d70a7798241f88d3",
            ),
        }[config.split]
        images = GDriveResource(id, file_name=f"WIDER_{config.split}.zip", sha256=sha256)

        anns = HttpResource(
            "http://shuoyang1213.me/WIDERFACE/support/bbx_annotation/wider_face_split.zip",
            sha256="c7561e4f5e7a118c249e0a5c5c902b0de90bbf120d7da9fa28d99041f68a8a5c",
        )
        return [images, anns]

    def _parse_test_annotation(self, data: str) -> Tuple[str, None]:
        return data.rsplit("/", 1)[1], None

    _BLUR_MAP = {
        "0": "clear",
        "1": "normal",
        "2": "heavy",
    }

    _EXPRESSION_MAP = {
        "0": "typical",
        "1": "exaggregate",
    }

    _ILLUMINATION_MAP = {
        "0": "normal",
        "1": "extreme",
    }

    _OCCLUSION_MAP = {
        "0": "no",
        "1": "partial",
        "2": "heavy",
    }

    _POSE_MAP = {
        "0": "typical",
        "1": "atypical",
    }

    def _prepare_anns(self, anns: Optional[List[Dict[str, Any]]], image_size: Tuple[int, int]) -> Dict[str, Any]:
        if not anns:
            return dict(
                zip(
                    ("bounding_boxes", "blur", "expression", "illumination", "occlusion", "pose", "invalid"),
                    itertools.repeat(None),
                )
            )

        return dict(
            bounding_boxes=BoundingBox(
                [[int(part) for part in (ann["x"], ann["y"], ann["w"], ann["h"])] for ann in anns],
                format="xywh",
                image_size=image_size,
            ),
            blur=[self._BLUR_MAP[ann["blur"]] for ann in anns],
            expression=[self._EXPRESSION_MAP[ann["expression"]] for ann in anns],
            illumination=[self._ILLUMINATION_MAP[ann["illumination"]] for ann in anns],
            occlusion=[self._OCCLUSION_MAP[ann["occlusion"]] for ann in anns],
            pose=[self._POSE_MAP[ann["pose"]] for ann in anns],
            invalid=[ann["invalid"] == "1" for ann in anns],
        )

    def _prepare_sample(
        self,
        data: Tuple[Tuple[str, Optional[List[Dict[str, Any]]]], Tuple[str, BinaryIO]],
    ) -> Dict[str, Any]:
        ann_data, image_data = data
        _, anns = ann_data
        path, buffer = image_data
        image = EncodedImage.from_file(buffer)

        return dict(
            self._prepare_anns(anns, image.image_size),
            path=path,
            label=Label.from_category(pathlib.Path(path).parent.name.rsplit("--")[1], categories=self.categories),
            image=image,
        )

    def _make_datapipe(
        self, resource_dps: List[IterDataPipe], *, config: DatasetConfig
    ) -> IterDataPipe[Dict[str, Any]]:
        images_dp, anns_dp = resource_dps

        if config.split == "test":
            anns_dp = Filter(anns_dp, path_comparator("name", "wider_face_test_filelist.txt"))
            anns_dp = LineReader(anns_dp, decode=True, return_path=False)
            anns_dp = Mapper(anns_dp, self._parse_test_annotation)
        else:
            anns_dp = Filter(anns_dp, path_comparator("name", f"wider_face_{config.split}_bbx_gt.txt"))
            anns_dp = LineReader(anns_dp, decode=True, return_path=False)
            anns_dp = WIDERFaceAnnotationParser(anns_dp)
        anns_dp = hint_sharding(anns_dp)
        anns_dp = hint_shuffling(anns_dp)

        dp = IterKeyZipper(
            anns_dp,
            images_dp,
            key_fn=getitem(0),
            ref_key_fn=path_accessor("name"),
            buffer_size=INFINITE_BUFFER_SIZE,
        )
        return Mapper(dp, self._prepare_sample)

    def _generate_categories(self, root: pathlib.Path) -> Tuple[str, ...]:
        resource = self.resources(self.default_config)[0]

        ids_and_categories = set(tuple(pathlib.Path(path).parent.name.split("--")) for path, _ in resource.load(root))
        _, categories = zip(*sorted(ids_and_categories, key=lambda id_and_category: int(id_and_category[0])))
        return categories
