import math
import numbers
import warnings
from typing import Any, Callable, cast, Dict, List, Optional, Tuple, Type, Union

import PIL.Image
import torch
from torch.utils._pytree import tree_flatten, tree_unflatten, TreeSpec
from torchvision.ops import masks_to_boxes
from torchvision.prototype import features
from torchvision.prototype.transforms import functional as F, InterpolationMode, Transform

from ._transform import _RandomApplyTransform
from .utils import check_type, has_any, query_chw, query_spatial_size


class RandomErasing(_RandomApplyTransform):
    _transformed_types = (features.is_simple_tensor, features.Image, PIL.Image.Image, features.Video)

    def __init__(
        self,
        p: float = 0.5,
        scale: Tuple[float, float] = (0.02, 0.33),
        ratio: Tuple[float, float] = (0.3, 3.3),
        value: float = 0,
        inplace: bool = False,
    ):
        super().__init__(p=p)
        if not isinstance(value, (numbers.Number, str, tuple, list)):
            raise TypeError("Argument value should be either a number or str or a sequence")
        if isinstance(value, str) and value != "random":
            raise ValueError("If value is str, it should be 'random'")
        if not isinstance(scale, (tuple, list)):
            raise TypeError("Scale should be a sequence")
        if not isinstance(ratio, (tuple, list)):
            raise TypeError("Ratio should be a sequence")
        if (scale[0] > scale[1]) or (ratio[0] > ratio[1]):
            warnings.warn("Scale and ratio should be of kind (min, max)")
        if scale[0] < 0 or scale[1] > 1:
            raise ValueError("Scale should be between 0 and 1")
        self.scale = scale
        self.ratio = ratio
        if isinstance(value, (int, float)):
            self.value = [value]
        elif isinstance(value, str):
            self.value = None
        elif isinstance(value, tuple):
            self.value = list(value)
        else:
            self.value = value
        self.inplace = inplace

        self._log_ratio = torch.log(torch.tensor(self.ratio))

    def _get_params(self, flat_inputs: List[Any]) -> Dict[str, Any]:
        img_c, img_h, img_w = query_chw(flat_inputs)

        if self.value is not None and not (len(self.value) in (1, img_c)):
            raise ValueError(
                f"If value is a sequence, it should have either a single value or {img_c} (number of inpt channels)"
            )

        area = img_h * img_w

        log_ratio = self._log_ratio
        for _ in range(10):
            erase_area = area * torch.empty(1).uniform_(self.scale[0], self.scale[1]).item()
            aspect_ratio = torch.exp(
                torch.empty(1).uniform_(
                    log_ratio[0],  # type: ignore[arg-type]
                    log_ratio[1],  # type: ignore[arg-type]
                )
            ).item()

            h = int(round(math.sqrt(erase_area * aspect_ratio)))
            w = int(round(math.sqrt(erase_area / aspect_ratio)))
            if not (h < img_h and w < img_w):
                continue

            if self.value is None:
                v = torch.empty([img_c, h, w], dtype=torch.float32).normal_()
            else:
                v = torch.tensor(self.value)[:, None, None]

            i = torch.randint(0, img_h - h + 1, size=(1,)).item()
            j = torch.randint(0, img_w - w + 1, size=(1,)).item()
            break
        else:
            i, j, h, w, v = 0, 0, img_h, img_w, None

        return dict(i=i, j=j, h=h, w=w, v=v)

    def _transform(
        self, inpt: Union[features.ImageType, features.VideoType], params: Dict[str, Any]
    ) -> Union[features.ImageType, features.VideoType]:
        if params["v"] is not None:
            inpt = F.erase(inpt, **params, inplace=self.inplace)

        return inpt


class _BaseMixupCutmix(_RandomApplyTransform):
    def __init__(self, alpha: float, p: float = 0.5) -> None:
        super().__init__(p=p)
        self.alpha = alpha
        self._dist = torch.distributions.Beta(torch.tensor([alpha]), torch.tensor([alpha]))

    def _check_inputs(self, flat_inputs: List[Any]) -> None:
        if not (
            has_any(flat_inputs, features.Image, features.Video, features.is_simple_tensor)
            and has_any(flat_inputs, features.OneHotLabel)
        ):
            raise TypeError(f"{type(self).__name__}() is only defined for tensor images/videos and one-hot labels.")
        if has_any(flat_inputs, PIL.Image.Image, features.BoundingBox, features.Mask, features.Label):
            raise TypeError(
                f"{type(self).__name__}() does not support PIL images, bounding boxes, masks and plain labels."
            )

    def _mixup_onehotlabel(self, inpt: features.OneHotLabel, lam: float) -> features.OneHotLabel:
        if inpt.ndim < 2:
            raise ValueError("Need a batch of one hot labels")
        output = inpt.roll(1, 0).mul_(1.0 - lam).add_(inpt.mul(lam))
        return features.OneHotLabel.wrap_like(inpt, output)


class RandomMixup(_BaseMixupCutmix):
    def _get_params(self, flat_inputs: List[Any]) -> Dict[str, Any]:
        return dict(lam=float(self._dist.sample(())))  # type: ignore[arg-type]

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        lam = params["lam"]
        if isinstance(inpt, (features.Image, features.Video)) or features.is_simple_tensor(inpt):
            expected_ndim = 5 if isinstance(inpt, features.Video) else 4
            if inpt.ndim < expected_ndim:
                raise ValueError("The transform expects a batched input")
            output = inpt.roll(1, 0).mul_(1.0 - lam).add_(inpt.mul(lam))

            if isinstance(inpt, (features.Image, features.Video)):
                output = type(inpt).wrap_like(inpt, output)  # type: ignore[arg-type]

            return output
        elif isinstance(inpt, features.OneHotLabel):
            return self._mixup_onehotlabel(inpt, lam)
        else:
            return inpt


class RandomCutmix(_BaseMixupCutmix):
    def _get_params(self, flat_inputs: List[Any]) -> Dict[str, Any]:
        lam = float(self._dist.sample(()))  # type: ignore[arg-type]

        H, W = query_spatial_size(flat_inputs)

        r_x = torch.randint(W, ())
        r_y = torch.randint(H, ())

        r = 0.5 * math.sqrt(1.0 - lam)
        r_w_half = int(r * W)
        r_h_half = int(r * H)

        x1 = int(torch.clamp(r_x - r_w_half, min=0))
        y1 = int(torch.clamp(r_y - r_h_half, min=0))
        x2 = int(torch.clamp(r_x + r_w_half, max=W))
        y2 = int(torch.clamp(r_y + r_h_half, max=H))
        box = (x1, y1, x2, y2)

        lam_adjusted = float(1.0 - (x2 - x1) * (y2 - y1) / (W * H))

        return dict(box=box, lam_adjusted=lam_adjusted)

    def _transform(self, inpt: Any, params: Dict[str, Any]) -> Any:
        if isinstance(inpt, (features.Image, features.Video)) or features.is_simple_tensor(inpt):
            box = params["box"]
            expected_ndim = 5 if isinstance(inpt, features.Video) else 4
            if inpt.ndim < expected_ndim:
                raise ValueError("The transform expects a batched input")
            x1, y1, x2, y2 = box
            rolled = inpt.roll(1, 0)
            output = inpt.clone()
            output[..., y1:y2, x1:x2] = rolled[..., y1:y2, x1:x2]

            if isinstance(inpt, (features.Image, features.Video)):
                output = inpt.wrap_like(inpt, output)  # type: ignore[arg-type]

            return output
        elif isinstance(inpt, features.OneHotLabel):
            lam_adjusted = params["lam_adjusted"]
            return self._mixup_onehotlabel(inpt, lam_adjusted)
        else:
            return inpt


def flatten_and_extract_data(
    inputs: Any, **target_types_or_checks: Tuple[Union[Type, Callable[[Any], bool]], ...]
) -> Tuple[Tuple[List[Any], TreeSpec, List[Dict[str, int]]], List[features.TensorImageType], List[Dict[str, Any]]]:
    # Images are special in the sense that they will always be extracted and returned
    # separately. Internally however, they behave just as the other features.
    types_or_checks: Dict[str, Tuple[Union[Type, Callable[[Any], bool]], ...]] = {
        "images": (features.Image, PIL.Image.Image, features.is_simple_tensor),
        **target_types_or_checks,
    }

    batch = inputs if len(inputs) > 1 else inputs[0]
    flat_batch = []
    sample_specs = []

    offset = 0
    batch_idcs = []
    batch_data = []
    for sample_idx, sample in enumerate(batch):
        flat_sample, sample_spec = tree_flatten(sample)
        flat_batch.extend(flat_sample)
        sample_specs.append(sample_spec)

        sample_types_or_checks = types_or_checks.copy()
        sample_idcs = {}
        sample_data = {}
        for flat_idx, item in enumerate(flat_sample, offset):
            if not sample_types_or_checks:
                break

            for key, types_or_checks_ in sample_types_or_checks.items():
                if check_type(item, types_or_checks_):
                    break
            else:
                continue

            del sample_types_or_checks[key]
            sample_idcs[key] = flat_idx
            sample_data[key] = item

        if sample_types_or_checks:
            # TODO: improve message
            raise TypeError(f"Sample at index {sample_idx} in the batch is missing {sample_types_or_checks.keys()}`")

        batch_idcs.append(sample_idcs)
        batch_data.append(sample_data)
        offset += len(flat_sample)

    batch_spec = TreeSpec(list, context=None, children_specs=sample_specs)

    targets = batch_data
    batch_data = []
    for target in targets:
        image = target.pop("images")
        if isinstance(image, features.Image):
            image = image.as_subclass(torch.Tensor)
        elif isinstance(image, PIL.Image.Image):
            image = F.pil_to_tensor(image)
        batch_data.append(image)

    return (flat_batch, batch_spec, batch_idcs), batch_data, targets


def unflatten_and_insert_data(
    flat_batch_with_spec: Tuple[List[Any], TreeSpec, List[Dict[str, int]]],
    images: List[features.TensorImageType],
    targets: List[Dict[str, Any]],
) -> Any:
    flat_batch, batch_spec, batch_idcs = flat_batch_with_spec

    for sample_idx, sample_idcs in enumerate(batch_idcs):
        for key, flat_idx in sample_idcs.items():
            item = images[sample_idx] if key == "images" else targets[sample_idx][key]

            inpt = flat_batch[flat_idx]
            if isinstance(inpt, features._Feature):
                item = type(inpt).wrap_like(inpt, item)
            elif isinstance(inpt, PIL.Image.Image):
                item = F.to_image_pil(item)

            flat_batch[flat_idx] = item

    return tree_unflatten(flat_batch, batch_spec)


class SimpleCopyPaste(_RandomApplyTransform):
    def __init__(
        self,
        p: float = 0.5,
        blending: bool = True,
        resize_interpolation: InterpolationMode = F.InterpolationMode.BILINEAR,
        antialias: Optional[bool] = None,
    ) -> None:
        super().__init__(p=p)
        self.resize_interpolation = resize_interpolation
        self.blending = blending
        self.antialias = antialias

    def _copy_paste(
        self,
        image: features.TensorImageType,
        target: Dict[str, Any],
        paste_image: features.TensorImageType,
        paste_target: Dict[str, Any],
        random_selection: torch.Tensor,
        blending: bool,
        resize_interpolation: F.InterpolationMode,
        antialias: Optional[bool],
    ) -> Tuple[features.TensorImageType, Dict[str, Any]]:
        paste_masks = paste_target["masks"].wrap_like(paste_target["masks"], paste_target["masks"][random_selection])
        paste_boxes = paste_target["boxes"].wrap_like(paste_target["boxes"], paste_target["boxes"][random_selection])
        paste_labels = paste_target["labels"].wrap_like(
            paste_target["labels"], paste_target["labels"][random_selection]
        )

        masks = target["masks"]

        # We resize source and paste data if they have different sizes
        # This is something different to TF implementation we introduced here as
        # originally the algorithm works on equal-sized data
        # (for example, coming from LSJ data augmentations)
        size1 = cast(List[int], image.shape[-2:])
        size2 = paste_image.shape[-2:]
        if size1 != size2:
            paste_image = F.resize(paste_image, size=size1, interpolation=resize_interpolation, antialias=antialias)
            paste_masks = F.resize(paste_masks, size=size1)
            paste_boxes = F.resize(paste_boxes, size=size1)

        paste_alpha_mask = paste_masks.sum(dim=0) > 0

        if blending:
            paste_alpha_mask = F.gaussian_blur(paste_alpha_mask.unsqueeze(0), kernel_size=[5, 5], sigma=[2.0])

        inverse_paste_alpha_mask = paste_alpha_mask.logical_not()
        # Copy-paste images:
        out_image = image.mul(inverse_paste_alpha_mask).add_(paste_image.mul(paste_alpha_mask))

        # Copy-paste masks:
        masks = masks * inverse_paste_alpha_mask
        non_all_zero_masks = masks.sum((-1, -2)) > 0
        masks = masks[non_all_zero_masks]

        # Do a shallow copy of the target dict
        out_target = {k: v for k, v in target.items()}

        out_target["masks"] = torch.cat([masks, paste_masks])

        # Copy-paste boxes and labels
        bbox_format = target["boxes"].format
        xyxy_boxes = masks_to_boxes(masks)
        # masks_to_boxes produces bboxes with x2y2 inclusive but x2y2 should be exclusive
        # we need to add +1 to x2y2.
        # There is a similar +1 in other reference implementations:
        # https://github.com/pytorch/vision/blob/b6feccbc4387766b76a3e22b13815dbbbfa87c0f/torchvision/models/detection/roi_heads.py#L418-L422
        xyxy_boxes[:, 2:] += 1
        boxes = F.convert_format_bounding_box(
            xyxy_boxes, old_format=features.BoundingBoxFormat.XYXY, new_format=bbox_format, inplace=True
        )
        out_target["boxes"] = torch.cat([boxes, paste_boxes])

        labels = target["labels"][non_all_zero_masks]
        out_target["labels"] = torch.cat([labels, paste_labels])

        # Check for degenerated boxes and remove them
        boxes = F.convert_format_bounding_box(
            out_target["boxes"], old_format=bbox_format, new_format=features.BoundingBoxFormat.XYXY
        )
        degenerate_boxes = boxes[:, 2:] <= boxes[:, :2]
        if degenerate_boxes.any():
            valid_targets = ~degenerate_boxes.any(dim=1)

            out_target["boxes"] = boxes[valid_targets]
            out_target["masks"] = out_target["masks"][valid_targets]
            out_target["labels"] = out_target["labels"][valid_targets]

        return out_image, out_target

    def forward(self, *inputs: Any) -> Any:
        flat_batch_with_spec, images, targets = flatten_and_extract_data(
            inputs,
            boxes=(features.BoundingBox,),
            masks=(features.Mask,),
            labels=(features.Label, features.OneHotLabel),
        )

        # images = [t1, t2, ..., tN]
        # Let's define paste_images as shifted list of input images
        # paste_images = [t2, t3, ..., tN, t1]
        # FYI: in TF they mix data on the dataset level
        images_rolled = images[-1:] + images[:-1]
        targets_rolled = targets[-1:] + targets[:-1]

        output_images, output_targets = [], []

        for image, target, paste_image, paste_target in zip(images, targets, images_rolled, targets_rolled):

            # Random paste targets selection:
            num_masks = len(paste_target["masks"])

            if num_masks < 1:
                # Such degerante case with num_masks=0 can happen with LSJ
                # Let's just return (image, target)
                output_image, output_target = image, target
            else:
                random_selection = torch.randint(0, num_masks, (num_masks,), device=paste_image.device)
                random_selection = torch.unique(random_selection)

                output_image, output_target = self._copy_paste(
                    image,
                    target,
                    paste_image,
                    paste_target,
                    random_selection=random_selection,
                    blending=self.blending,
                    resize_interpolation=self.resize_interpolation,
                    antialias=self.antialias,
                )
            output_images.append(output_image)
            output_targets.append(output_target)

        return unflatten_and_insert_data(flat_batch_with_spec, output_images, output_targets)


class MixupDetection(Transform):
    _transformed_types = (features.is_simple_tensor, features.Image, PIL.Image)

    def __init__(
        self,
        *,
        alpha: float = 1.5,
    ) -> None:
        super().__init__()
        self._dist = torch.distributions.Beta(torch.tensor([alpha]), torch.tensor([alpha]))

    def _check_inputs(self, flat_inputs: List[Any]) -> None:
        if has_any(flat_inputs, features.Mask, features.Video):
            raise TypeError(f"{type(self).__name__}() is only supported for images and bounding boxes.")

        if not (
            has_any(flat_inputs, features.Image, PIL.Image.Image, features.is_simple_tensor)
            and has_any(flat_inputs, features.BoundingBox)
        ):
            raise TypeError(f"{type(self).__name__}() is only defined for tensor images and bounding boxes.")

    def forward(self, *inputs: Any) -> Any:
        flat_batch_with_spec, images, targets = flatten_and_extract_data(
            inputs,
            boxes=(features.BoundingBox,),
            labels=(features.Label, features.OneHotLabel),
        )
        # TODO: refactor this since we have already extracted the images and boxes
        self._check_inputs(flat_batch_with_spec[0])

        # images = [t1, t2, ..., tN]
        # Let's define paste_images as shifted list of input images
        # paste_images = [tN, t1, ..., tN-1,]
        images_rolled = images[-1:] + images[:-1]
        targets_rolled = targets[-1:] + targets[:-1]

        output_images, output_targets = [], []
        for image_1, target_1, image_2, target_2 in zip(images, targets, images_rolled, targets_rolled):
            output_image, output_target = self._mixup(
                image_1,
                target_1,
                image_2,
                target_2,
            )
            output_images.append(output_image)
            output_targets.append(output_target)

        return unflatten_and_insert_data(flat_batch_with_spec, output_images, output_targets)

    def _mixup(
        self,
        image_1: features.TensorImageType,
        target_1: Dict[str, Any],
        image_2: features.TensorImageType,
        target_2: Dict[str, Any],
    ) -> Tuple[features.TensorImageType, Dict[str, Any]]:
        """
        Performs mixup on the given images and targets.
        """
        mixup_ratio = self._dist.sample().item()
        print(mixup_ratio)

        c_1, h_1, w_1 = image_1.shape
        c_2, h_2, w_2 = image_2.shape
        h_mixup = max(h_1, h_2)
        w_mixup = max(w_1, w_2)

        if mixup_ratio >= 1.0:
            return image_1, target_1

        # mixup images and prevent the object aspect ratio from changing
        mix_img = torch.zeros(c_1, h_mixup, w_mixup, dtype=torch.float32)
        mix_img[:, : image_1.shape[1], : image_1.shape[2]] = image_1 * mixup_ratio
        mix_img[:, : image_2.shape[1], : image_2.shape[2]] += image_2 * (1.0 - mixup_ratio)
        # mixup targets
        mix_target = {**target_1, **target_2}
        box_format = target_1["boxes"].format
        mixed_boxes = {
            "boxes": features.BoundingBox(
                torch.vstack((target_1["boxes"], target_2["boxes"])),
                format=box_format,
                spatial_size=(h_mixup, w_mixup),
            )
        }
        mix_labels = {"labels": torch.cat((target_1["labels"], target_2["labels"]))}
        mix_target.update(mixed_boxes)
        mix_target.update(mix_labels)

        return mix_img, mix_target
