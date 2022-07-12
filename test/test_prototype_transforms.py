import itertools

import pytest
import torch
from common_utils import assert_equal
from test_prototype_transforms_functional import (
    make_images,
    make_bounding_boxes,
    make_bounding_box,
    make_one_hot_labels,
    make_label,
    make_segmentation_mask,
)
from torchvision.prototype import transforms, features
from torchvision.transforms.functional import to_pil_image, pil_to_tensor, InterpolationMode


def make_vanilla_tensor_images(*args, **kwargs):
    for image in make_images(*args, **kwargs):
        if image.ndim > 3:
            continue
        yield image.data


def make_pil_images(*args, **kwargs):
    for image in make_vanilla_tensor_images(*args, **kwargs):
        yield to_pil_image(image)


def make_vanilla_tensor_bounding_boxes(*args, **kwargs):
    for bounding_box in make_bounding_boxes(*args, **kwargs):
        yield bounding_box.data


def parametrize(transforms_with_inputs):
    return pytest.mark.parametrize(
        ("transform", "input"),
        [
            pytest.param(
                transform,
                input,
                id=f"{type(transform).__name__}-{type(input).__module__}.{type(input).__name__}-{idx}",
            )
            for transform, inputs in transforms_with_inputs
            for idx, input in enumerate(inputs)
        ],
    )


def parametrize_from_transforms(*transforms):
    transforms_with_inputs = []
    for transform in transforms:
        for creation_fn in [
            make_images,
            make_bounding_boxes,
            make_one_hot_labels,
            make_vanilla_tensor_images,
            make_pil_images,
        ]:
            inputs = list(creation_fn())
            try:
                output = transform(inputs[0])
            except Exception:
                continue
            else:
                if output is inputs[0]:
                    continue

            transforms_with_inputs.append((transform, inputs))

    return parametrize(transforms_with_inputs)


class TestSmoke:
    @parametrize_from_transforms(
        transforms.RandomErasing(p=1.0),
        transforms.Resize([16, 16]),
        transforms.CenterCrop([16, 16]),
        transforms.ConvertImageDtype(),
        transforms.RandomHorizontalFlip(),
        transforms.Pad(5),
        transforms.RandomZoomOut(),
        transforms.RandomRotation(degrees=(-45, 45)),
        transforms.RandomAffine(degrees=(-45, 45)),
    )
    def test_common(self, transform, input):
        transform(input)

    @parametrize(
        [
            (
                transform,
                [
                    dict(
                        image=features.Image.new_like(image, image.unsqueeze(0), dtype=torch.float),
                        one_hot_label=features.OneHotLabel.new_like(
                            one_hot_label, one_hot_label.unsqueeze(0), dtype=torch.float
                        ),
                    )
                    for image, one_hot_label in itertools.product(make_images(), make_one_hot_labels())
                ],
            )
            for transform in [
                transforms.RandomMixup(alpha=1.0),
                transforms.RandomCutmix(alpha=1.0),
            ]
        ]
    )
    def test_mixup_cutmix(self, transform, input):
        transform(input)

        # add other data that should bypass and wont raise any error
        input_copy = dict(input)
        input_copy["path"] = "/path/to/somewhere"
        input_copy["num"] = 1234
        transform(input_copy)

        # Check if we raise an error if sample contains bbox or mask or label
        err_msg = "does not support bounding boxes, segmentation masks and plain labels"
        input_copy = dict(input)
        for unsup_data in [make_label(), make_bounding_box(format="XYXY"), make_segmentation_mask()]:
            input_copy["unsupported"] = unsup_data
            with pytest.raises(TypeError, match=err_msg):
                transform(input_copy)

    @parametrize(
        [
            (
                transform,
                itertools.chain.from_iterable(
                    fn(
                        color_spaces=[
                            features.ColorSpace.GRAY,
                            features.ColorSpace.RGB,
                        ],
                        dtypes=[torch.uint8],
                        extra_dims=[(4,)],
                    )
                    for fn in [
                        make_images,
                        make_vanilla_tensor_images,
                        make_pil_images,
                    ]
                ),
            )
            for transform in (
                transforms.RandAugment(),
                transforms.TrivialAugmentWide(),
                transforms.AutoAugment(),
                transforms.AugMix(),
            )
        ]
    )
    def test_auto_augment(self, transform, input):
        transform(input)

    @parametrize(
        [
            (
                transforms.Normalize(mean=[0.0, 0.0, 0.0], std=[1.0, 1.0, 1.0]),
                itertools.chain.from_iterable(
                    fn(color_spaces=[features.ColorSpace.RGB], dtypes=[torch.float32])
                    for fn in [
                        make_images,
                        make_vanilla_tensor_images,
                    ]
                ),
            ),
        ]
    )
    def test_normalize(self, transform, input):
        transform(input)

    @parametrize(
        [
            (
                transforms.RandomResizedCrop([16, 16]),
                itertools.chain(
                    make_images(extra_dims=[(4,)]),
                    make_vanilla_tensor_images(),
                    make_pil_images(),
                ),
            )
        ]
    )
    def test_random_resized_crop(self, transform, input):
        transform(input)

    @parametrize(
        [
            (
                transforms.ConvertImageColorSpace(color_space=new_color_space, old_color_space=old_color_space),
                itertools.chain.from_iterable(
                    [
                        fn(color_spaces=[old_color_space])
                        for fn in (
                            make_images,
                            make_vanilla_tensor_images,
                            make_pil_images,
                        )
                    ]
                ),
            )
            for old_color_space, new_color_space in itertools.product(
                [
                    features.ColorSpace.GRAY,
                    features.ColorSpace.GRAY_ALPHA,
                    features.ColorSpace.RGB,
                    features.ColorSpace.RGB_ALPHA,
                ],
                repeat=2,
            )
        ]
    )
    def test_convert_image_color_space(self, transform, input):
        transform(input)


@pytest.mark.parametrize("p", [0.0, 1.0])
class TestRandomHorizontalFlip:
    def input_expected_image_tensor(self, p, dtype=torch.float32):
        input = torch.tensor([[[0, 1], [0, 1]], [[1, 0], [1, 0]]], dtype=dtype)
        expected = torch.tensor([[[1, 0], [1, 0]], [[0, 1], [0, 1]]], dtype=dtype)

        return input, expected if p == 1 else input

    def test_simple_tensor(self, p):
        input, expected = self.input_expected_image_tensor(p)
        transform = transforms.RandomHorizontalFlip(p=p)

        actual = transform(input)

        assert_equal(expected, actual)

    def test_pil_image(self, p):
        input, expected = self.input_expected_image_tensor(p, dtype=torch.uint8)
        transform = transforms.RandomHorizontalFlip(p=p)

        actual = transform(to_pil_image(input))

        assert_equal(expected, pil_to_tensor(actual))

    def test_features_image(self, p):
        input, expected = self.input_expected_image_tensor(p)
        transform = transforms.RandomHorizontalFlip(p=p)

        actual = transform(features.Image(input))

        assert_equal(features.Image(expected), actual)

    def test_features_segmentation_mask(self, p):
        input, expected = self.input_expected_image_tensor(p)
        transform = transforms.RandomHorizontalFlip(p=p)

        actual = transform(features.SegmentationMask(input))

        assert_equal(features.SegmentationMask(expected), actual)

    def test_features_bounding_box(self, p):
        input = features.BoundingBox([0, 0, 5, 5], format=features.BoundingBoxFormat.XYXY, image_size=(10, 10))
        transform = transforms.RandomHorizontalFlip(p=p)

        actual = transform(input)

        expected_image_tensor = torch.tensor([5, 0, 10, 5]) if p == 1.0 else input
        expected = features.BoundingBox.new_like(input, data=expected_image_tensor)
        assert_equal(expected, actual)
        assert actual.format == expected.format
        assert actual.image_size == expected.image_size


@pytest.mark.parametrize("p", [0.0, 1.0])
class TestRandomVerticalFlip:
    def input_expected_image_tensor(self, p, dtype=torch.float32):
        input = torch.tensor([[[1, 1], [0, 0]], [[1, 1], [0, 0]]], dtype=dtype)
        expected = torch.tensor([[[0, 0], [1, 1]], [[0, 0], [1, 1]]], dtype=dtype)

        return input, expected if p == 1 else input

    def test_simple_tensor(self, p):
        input, expected = self.input_expected_image_tensor(p)
        transform = transforms.RandomVerticalFlip(p=p)

        actual = transform(input)

        assert_equal(expected, actual)

    def test_pil_image(self, p):
        input, expected = self.input_expected_image_tensor(p, dtype=torch.uint8)
        transform = transforms.RandomVerticalFlip(p=p)

        actual = transform(to_pil_image(input))

        assert_equal(expected, pil_to_tensor(actual))

    def test_features_image(self, p):
        input, expected = self.input_expected_image_tensor(p)
        transform = transforms.RandomVerticalFlip(p=p)

        actual = transform(features.Image(input))

        assert_equal(features.Image(expected), actual)

    def test_features_segmentation_mask(self, p):
        input, expected = self.input_expected_image_tensor(p)
        transform = transforms.RandomVerticalFlip(p=p)

        actual = transform(features.SegmentationMask(input))

        assert_equal(features.SegmentationMask(expected), actual)

    def test_features_bounding_box(self, p):
        input = features.BoundingBox([0, 0, 5, 5], format=features.BoundingBoxFormat.XYXY, image_size=(10, 10))
        transform = transforms.RandomVerticalFlip(p=p)

        actual = transform(input)

        expected_image_tensor = torch.tensor([0, 5, 5, 10]) if p == 1.0 else input
        expected = features.BoundingBox.new_like(input, data=expected_image_tensor)
        assert_equal(expected, actual)
        assert actual.format == expected.format
        assert actual.image_size == expected.image_size


class TestPad:
    def test_assertions(self):
        with pytest.raises(TypeError, match="Got inappropriate padding arg"):
            transforms.Pad("abc")

        with pytest.raises(ValueError, match="Padding must be an int or a 1, 2, or 4"):
            transforms.Pad([-0.7, 0, 0.7])

        with pytest.raises(TypeError, match="Got inappropriate fill arg"):
            transforms.Pad(12, fill="abc")

        with pytest.raises(ValueError, match="Padding mode should be either"):
            transforms.Pad(12, padding_mode="abc")

    @pytest.mark.parametrize("padding", [1, (1, 2), [1, 2, 3, 4]])
    @pytest.mark.parametrize("fill", [0, [1, 2, 3], (2, 3, 4)])
    @pytest.mark.parametrize("padding_mode", ["constant", "edge"])
    def test__transform(self, padding, fill, padding_mode, mocker):
        transform = transforms.Pad(padding, fill=fill, padding_mode=padding_mode)

        fn = mocker.patch("torchvision.prototype.transforms.functional.pad")
        inpt = mocker.MagicMock(spec=torch.Tensor)
        _ = transform(inpt)

        fn.assert_called_once_with(inpt, padding=padding, fill=fill, padding_mode=padding_mode)


class TestRandomZoomOut:
    def test_assertions(self):
        with pytest.raises(TypeError, match="Got inappropriate fill arg"):
            transforms.RandomZoomOut(fill="abc")

        with pytest.raises(TypeError, match="should be a sequence of length"):
            transforms.RandomZoomOut(0, side_range=0)

        with pytest.raises(ValueError, match="Invalid canvas side range"):
            transforms.RandomZoomOut(0, side_range=[4.0, 1.0])

    @pytest.mark.parametrize("fill", [0, [1, 2, 3], (2, 3, 4)])
    @pytest.mark.parametrize("side_range", [(1.0, 4.0), [2.0, 5.0]])
    def test__get_params(self, fill, side_range):
        transform = transforms.RandomZoomOut(fill=fill, side_range=side_range)

        image = features.Image(torch.rand(1, 3, 32, 32))
        c, h, w = image.shape[-3:]

        params = transform._get_params(image)

        assert params["fill"] == (fill if not isinstance(fill, int) else [fill] * c)
        assert len(params["padding"]) == 4
        assert 0 <= params["padding"][0] <= (side_range[1] - 1) * w
        assert 0 <= params["padding"][1] <= (side_range[1] - 1) * h
        assert 0 <= params["padding"][2] <= (side_range[1] - 1) * w
        assert 0 <= params["padding"][3] <= (side_range[1] - 1) * h

    @pytest.mark.parametrize("fill", [0, [1, 2, 3], (2, 3, 4)])
    @pytest.mark.parametrize("side_range", [(1.0, 4.0), [2.0, 5.0]])
    def test__transform(self, fill, side_range, mocker):
        image = features.Image(torch.rand(1, 3, 32, 32))
        transform = transforms.RandomZoomOut(fill=fill, side_range=side_range, p=1)

        fn = mocker.patch("torchvision.prototype.transforms.functional.pad")
        # vfdev-5, Feature Request: let's store params as Transform attribute
        # This could be also helpful for users
        torch.manual_seed(12)
        _ = transform(image)
        torch.manual_seed(12)
        torch.rand(1)  # random apply changes random state
        params = transform._get_params(image)

        fn.assert_called_once_with(image, **params)


class TestRandomRotation:
    def test_assertions(self):
        with pytest.raises(ValueError, match="is a single number, it must be positive"):
            transforms.RandomRotation(-0.7)

        for d in [[-0.7], [-0.7, 0, 0.7]]:
            with pytest.raises(ValueError, match="degrees should be a sequence of length 2"):
                transforms.RandomRotation(d)

        with pytest.raises(TypeError, match="Got inappropriate fill arg"):
            transforms.RandomRotation(12, fill="abc")

        with pytest.raises(TypeError, match="center should be a sequence of length"):
            transforms.RandomRotation(12, center=12)

        with pytest.raises(ValueError, match="center should be a sequence of length"):
            transforms.RandomRotation(12, center=[1, 2, 3])

    def test__get_params(self):
        angle_bound = 34
        transform = transforms.RandomRotation(angle_bound)

        params = transform._get_params(None)
        assert -angle_bound <= params["angle"] <= angle_bound

        angle_bounds = [12, 34]
        transform = transforms.RandomRotation(angle_bounds)

        params = transform._get_params(None)
        assert angle_bounds[0] <= params["angle"] <= angle_bounds[1]

    @pytest.mark.parametrize("degrees", [23, [0, 45], (0, 45)])
    @pytest.mark.parametrize("expand", [False, True])
    @pytest.mark.parametrize("fill", [0, [1, 2, 3], (2, 3, 4)])
    @pytest.mark.parametrize("center", [None, [2.0, 3.0]])
    def test__transform(self, degrees, expand, fill, center, mocker):
        interpolation = InterpolationMode.BILINEAR
        transform = transforms.RandomRotation(
            degrees, interpolation=interpolation, expand=expand, fill=fill, center=center
        )

        if isinstance(degrees, (tuple, list)):
            assert transform.degrees == [float(degrees[0]), float(degrees[1])]
        else:
            assert transform.degrees == [float(-degrees), float(degrees)]

        fn = mocker.patch("torchvision.prototype.transforms.functional.rotate")
        inpt = mocker.MagicMock(spec=torch.Tensor)
        # vfdev-5, Feature Request: let's store params as Transform attribute
        # This could be also helpful for users
        torch.manual_seed(12)
        _ = transform(inpt)
        torch.manual_seed(12)
        params = transform._get_params(inpt)

        fn.assert_called_once_with(inpt, **params, interpolation=interpolation, expand=expand, fill=fill, center=center)


class TestRandomAffine(TestRandomRotation):
    def test_assertions(self):
        with pytest.raises(ValueError, match="is a single number, it must be positive"):
            transforms.RandomAffine(-0.7)

        for d in [[-0.7], [-0.7, 0, 0.7]]:
            with pytest.raises(ValueError, match="degrees should be a sequence of length 2"):
                transforms.RandomAffine(d)

        with pytest.raises(TypeError, match="Got inappropriate fill arg"):
            transforms.RandomAffine(12, fill="abc")

        with pytest.raises(TypeError, match="Got inappropriate fill arg"):
            transforms.RandomAffine(12, fill="abc")

        for kwargs in [
            {"center": 12},
            {"translate": 12},
            {"scale": 12},
        ]:
            with pytest.raises(TypeError, match="should be a sequence of length"):
                transforms.RandomAffine(12, **kwargs)

        for kwargs in [{"center": [1, 2, 3]}, {"translate": [1, 2, 3]}, {"scale": [1, 2, 3]}]:
            with pytest.raises(ValueError, match="should be a sequence of length"):
                transforms.RandomAffine(12, **kwargs)

        with pytest.raises(ValueError, match="translation values should be between 0 and 1"):
            transforms.RandomAffine(12, translate=[-1.0, 2.0])

        with pytest.raises(ValueError, match="scale values should be positive"):
            transforms.RandomAffine(12, scale=[-1.0, 2.0])

        with pytest.raises(ValueError, match="is a single number, it must be positive"):
            transforms.RandomAffine(12, shear=-10)

        for s in [[-0.7], [-0.7, 0, 0.7]]:
            with pytest.raises(ValueError, match="shear should be a sequence of length 2"):
                transforms.RandomAffine(12, shear=s)

    @pytest.mark.parametrize("degrees", [23, [0, 45], (0, 45)])
    @pytest.mark.parametrize("translate", [None, [0.1, 0.2]])
    @pytest.mark.parametrize("scale", [None, [0.7, 1.2]])
    @pytest.mark.parametrize("shear", [None, 2.0, [5.0, 15.0], [1.0, 2.0, 3.0, 4.0]])
    def test__get_params(self, degrees, translate, scale, shear):
        image = features.Image(torch.rand(1, 3, 32, 32))
        h, w = image.shape[-2:]

        transform = transforms.RandomAffine(degrees, translate=translate, scale=scale, shear=shear)
        params = transform._get_params(image)

        if not isinstance(degrees, (list, tuple)):
            assert -degrees <= params["angle"] <= degrees
        else:
            assert degrees[0] <= params["angle"] <= degrees[1]

        if translate is not None:
            assert -translate[0] * w <= params["translations"][0] <= translate[0] * w
            assert -translate[1] * h <= params["translations"][1] <= translate[1] * h
        else:
            assert params["translations"] == (0, 0)

        if scale is not None:
            assert scale[0] <= params["scale"] <= scale[1]
        else:
            assert params["scale"] == 1.0

        if shear is not None:
            if isinstance(shear, float):
                assert -shear <= params["shear"][0] <= shear
                assert params["shear"][1] == 0.0
            elif len(shear) == 2:
                assert shear[0] <= params["shear"][0] <= shear[1]
                assert params["shear"][1] == 0.0
            else:
                assert shear[0] <= params["shear"][0] <= shear[1]
                assert shear[2] <= params["shear"][1] <= shear[3]
        else:
            assert params["shear"] == (0, 0)

    @pytest.mark.parametrize("degrees", [23, [0, 45], (0, 45)])
    @pytest.mark.parametrize("translate", [None, [0.1, 0.2]])
    @pytest.mark.parametrize("scale", [None, [0.7, 1.2]])
    @pytest.mark.parametrize("shear", [None, 2.0, [5.0, 15.0], [1.0, 2.0, 3.0, 4.0]])
    @pytest.mark.parametrize("fill", [0, [1, 2, 3], (2, 3, 4)])
    @pytest.mark.parametrize("center", [None, [2.0, 3.0]])
    def test__transform(self, degrees, translate, scale, shear, fill, center, mocker):
        interpolation = InterpolationMode.BILINEAR
        transform = transforms.RandomAffine(
            degrees,
            translate=translate,
            scale=scale,
            shear=shear,
            interpolation=interpolation,
            fill=fill,
            center=center,
        )

        if isinstance(degrees, (tuple, list)):
            assert transform.degrees == [float(degrees[0]), float(degrees[1])]
        else:
            assert transform.degrees == [float(-degrees), float(degrees)]

        fn = mocker.patch("torchvision.prototype.transforms.functional.affine")
        inpt = features.Image(torch.rand(1, 3, 32, 32))
        # vfdev-5, Feature Request: let's store params as Transform attribute
        # This could be also helpful for users
        torch.manual_seed(12)
        _ = transform(inpt)
        torch.manual_seed(12)
        params = transform._get_params(inpt)

        fn.assert_called_once_with(inpt, **params, interpolation=interpolation, fill=fill, center=center)
