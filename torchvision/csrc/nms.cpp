#include "nms.h"

#include <torch/autograd.h>
#include <torch/types.h>

#if defined(WITH_CUDA) || defined(WITH_HIP)
#include <ATen/autocast_mode.h>
#endif

namespace vision {
namespace ops {

at::Tensor nms(
    const at::Tensor& dets,
    const at::Tensor& scores,
    double iou_threshold) {
  static auto op = c10::Dispatcher::singleton()
                       .findSchemaOrThrow("torchvision::nms", "")
                       .typed<decltype(nms)>();
  return op.call(dets, scores, iou_threshold);
}

#if defined(WITH_CUDA) || defined(WITH_HIP)
at::Tensor nms_autocast(
    const at::Tensor& dets,
    const at::Tensor& scores,
    double iou_threshold) {
  c10::impl::ExcludeDispatchKeyGuard no_autocast(c10::DispatchKey::Autocast);
  return nms(
      at::autocast::cached_cast(at::kFloat, dets),
      at::autocast::cached_cast(at::kFloat, scores),
      iou_threshold);
}

TORCH_LIBRARY_IMPL(torchvision, Autocast, m) {
  m.impl("nms", nms_autocast);
}
#endif

TORCH_LIBRARY_FRAGMENT(torchvision, m) {
  m.def("nms(Tensor dets, Tensor scores, float iou_threshold) -> Tensor");
}

} // namespace ops
} // namespace vision
