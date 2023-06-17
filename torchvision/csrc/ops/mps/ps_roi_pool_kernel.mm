#include <ATen/mps/MPSProfiler.h>
#include <ATen/native/mps/OperationUtils.h>
#include "vision_kernels.h"
#include "mps_helpers.h"

#include <iostream>
#include <cmath>

namespace vision {
namespace ops {

namespace {

std::tuple<at::Tensor, at::Tensor> ps_roi_pool_forward_kernel(
    const at::Tensor& input,
    const at::Tensor& rois,
    double spatial_scale,
    int64_t pooled_height,
    int64_t pooled_width) {

  using namespace at::native::mps;
  TORCH_CHECK(input.is_mps(), "input must be a MPS tensor");
  TORCH_CHECK(rois.is_mps(), "rois must be a MPS tensor");
  TORCH_CHECK(rois.size(1) == 5, "rois must have shape as Tensor[K, 5]");

  at::TensorArg input_t{input, "input", 1}, rois_t{rois, "rois", 2};

  at::CheckedFrom c = "ps_roi_pool_forward_kernel";
  at::checkAllSameGPU(c, {input_t, rois_t});
  at::checkAllSameType(c, {input_t, rois_t});
  
  int64_t num_rois = rois.size(0);
  int64_t channels = input.size(1);
  int64_t height = input.size(2);
  int64_t width = input.size(3);
  float spatial_scale_f = static_cast<float>(spatial_scale);
  
  TORCH_CHECK(
      channels % (pooled_height * pooled_width) == 0,
      "input channels must be a multiple of pooling height * pooling width");
  int64_t channels_out = channels / (pooled_height * pooled_width);

  auto output = at::zeros(
      {num_rois, channels_out, pooled_height, pooled_width}, input.options());
  auto channel_mapping =
      at::zeros(output.sizes(), input.options().dtype(at::kLong));
  auto output_size = output.numel();

  if (output_size == 0) {
    return std::make_tuple(output, channel_mapping);
  }

  auto input_ = input.contiguous();
  auto rois_ = rois.contiguous();

  id<MTLBuffer> inputBuffer = getMTLBufferStorage(input_);
  id<MTLBuffer> roisBuffer = getMTLBufferStorage(rois_);
  id<MTLBuffer> outputBuffer = getMTLBufferStorage(output);
  id<MTLBuffer> channelMappingBuffer = getMTLBufferStorage(channel_mapping);
  id<MTLDevice> device = MPSDevice::getInstance()->device();
  MPSStream* mpsStream = getCurrentMPSStream();
  dispatch_sync(mpsStream->queue(), ^() {
    @autoreleasepool {
      id<MTLComputeCommandEncoder> computeEncoder = mpsStream->commandEncoder();
      MTLSize threadgroupsPerGrid = MTLSizeMake(std::min(ceil_div(static_cast<int64_t>(output_size), static_cast<int64_t>(512)), static_cast<int64_t>(4096)), 1, 1);

      const std::string kernel = "ps_roi_pool_" + scalarToMetalTypeString(input.scalar_type());
      id<MTLComputePipelineState> binaryPSO = mps::binaryPipelineState(device, kernel);

      // this function call is a no-op if MPS Profiler is not enabled
      getMPSProfiler().beginProfileKernel(binaryPSO, kernel, {input_, rois_});

      [computeEncoder setComputePipelineState:binaryPSO];
      // [N, C, H, W]
      [computeEncoder setBuffer:inputBuffer offset:input_.storage_offset() * input_.element_size() atIndex:0];
      [computeEncoder setBuffer:roisBuffer offset:rois_.storage_offset() * rois_.element_size() atIndex:1];
      [computeEncoder setBuffer:outputBuffer offset:output.storage_offset() * output.element_size() atIndex:2];
      [computeEncoder setBuffer:channelMappingBuffer offset:channel_mapping.storage_offset() * channel_mapping.element_size() atIndex:3];
      
      [computeEncoder setBytes:&output_size length:sizeof(int64_t) atIndex:4];
      [computeEncoder setBytes:&channels length:sizeof(int64_t) atIndex:5];
      [computeEncoder setBytes:&height length:sizeof(int64_t) atIndex:6];
      [computeEncoder setBytes:&width length:sizeof(int64_t) atIndex:7];
      [computeEncoder setBytes:&pooled_height length:sizeof(int64_t) atIndex:8];
      [computeEncoder setBytes:&pooled_width length:sizeof(int64_t) atIndex:9];
      [computeEncoder setBytes:&channels_out length:sizeof(int64_t) atIndex:10];
      [computeEncoder setBytes:&spatial_scale_f length:sizeof(float) atIndex:11];

      // A threadGroup is equivalent to a cuda's block.
      NSUInteger tgSize = binaryPSO.maxTotalThreadsPerThreadgroup;
      if (tgSize > threadsPerBlock) {
        tgSize = threadsPerBlock;
      }

      MTLSize threadGroupSize = MTLSizeMake(tgSize, 1, 1);
      [computeEncoder dispatchThreadgroups:threadgroupsPerGrid threadsPerThreadgroup:threadGroupSize];

      getMPSProfiler().endProfileKernel(binaryPSO);
    }
  });
  return std::make_tuple(output, channel_mapping);
}

at::Tensor ps_roi_pool_backward_kernel(
    const at::Tensor& grad,
    const at::Tensor& rois,
    const at::Tensor& argmax,
    double spatial_scale,
    int64_t pooled_height,
    int64_t pooled_width,
    int64_t batch_size,
    int64_t channels,
    int64_t height,
    int64_t width) {

  using namespace at::native::mps;
  TORCH_CHECK(grad.is_mps(), "grad must be a MPS tensor");
  TORCH_CHECK(rois.is_mps(), "rois must be a MPS tensor");
  TORCH_CHECK(argmax.is_mps(), "argmax must be a MPS tensor");

  at::TensorArg grad_t{grad, "input", 1}, rois_t{rois, "rois", 2}, argmax_t{argmax, "argmax", 3};

  at::CheckedFrom c = "ps_roi_pool_backward_kernel";
  at::checkAllSameGPU(c, {grad_t, rois_t, argmax_t});
  at::checkAllSameType(c, {grad_t, rois_t});
  
  float spatial_scale_f = static_cast<float>(spatial_scale);

  at::Tensor grad_input = at::zeros(
      {batch_size, channels, height, width}, grad.options());

  if (grad.numel() == 0) {
    return grad_input;
  }

  int64_t n_stride = grad.stride(0);
  int64_t c_stride = grad.stride(1);
  int64_t h_stride = grad.stride(2);
  int64_t w_stride = grad.stride(3);
  int64_t output_size = grad.numel();

  at::globalContext().alertNotDeterministic("ps_roi_pool_backward_kernel");
  auto argmax_ = argmax.contiguous(), rois_ = rois.contiguous();

  id<MTLBuffer> inputBuffer = getMTLBufferStorage(grad);
  id<MTLBuffer> roisBuffer = getMTLBufferStorage(rois_);
  id<MTLBuffer> argmaxBuffer = getMTLBufferStorage(argmax_);
  id<MTLBuffer> outputBuffer = getMTLBufferStorage(grad_input);
  id<MTLDevice> device = MPSDevice::getInstance()->device();
  MPSStream* mpsStream = getCurrentMPSStream();
  dispatch_sync(mpsStream->queue(), ^() {
    @autoreleasepool {
      id<MTLComputeCommandEncoder> computeEncoder = mpsStream->commandEncoder();
      MTLSize threadgroupsPerGrid = MTLSizeMake(std::min(ceil_div(static_cast<int64_t>(grad.numel()), static_cast<int64_t>(512)), static_cast<int64_t>(4096)), 1, 1);

      const std::string kernel = "ps_roi_pool_backward_" + scalarToMetalTypeString(grad.scalar_type());
      id<MTLComputePipelineState> binaryPSO = mps::binaryPipelineState(device, kernel);

      // this function call is a no-op if MPS Profiler is not enabled
      getMPSProfiler().beginProfileKernel(binaryPSO, kernel, {grad, rois_, argmax_});

      [computeEncoder setComputePipelineState:binaryPSO];
      // [N, C, H, W]
      [computeEncoder setBuffer:inputBuffer offset:grad.storage_offset() * grad.element_size() atIndex:0];
      [computeEncoder setBuffer:roisBuffer offset:rois_.storage_offset() * rois_.element_size() atIndex:1];
      [computeEncoder setBuffer:argmaxBuffer offset:argmax_.storage_offset() * argmax_.element_size() atIndex:2];
      [computeEncoder setBuffer:outputBuffer offset:grad_input.storage_offset() * grad_input.element_size() atIndex:3];
      
      [computeEncoder setBytes:&output_size length:sizeof(int64_t) atIndex:4];
      [computeEncoder setBytes:&channels length:sizeof(int64_t) atIndex:5];
      [computeEncoder setBytes:&height length:sizeof(int64_t) atIndex:6];
      [computeEncoder setBytes:&width length:sizeof(int64_t) atIndex:7];
      [computeEncoder setBytes:&pooled_height length:sizeof(int64_t) atIndex:8];
      [computeEncoder setBytes:&pooled_width length:sizeof(int64_t) atIndex:9];
      [computeEncoder setBytes:&spatial_scale_f length:sizeof(float) atIndex:10];
      [computeEncoder setBytes:&n_stride length:sizeof(int64_t) atIndex:11];
      [computeEncoder setBytes:&c_stride length:sizeof(int64_t) atIndex:12];
      [computeEncoder setBytes:&h_stride length:sizeof(int64_t) atIndex:13];
      [computeEncoder setBytes:&w_stride length:sizeof(int64_t) atIndex:14];

      // A threadGroup is equivalent to a cuda's block.
      NSUInteger tgSize = binaryPSO.maxTotalThreadsPerThreadgroup;
      if (tgSize > threadsPerBlock) {
        tgSize = threadsPerBlock;
      }

      MTLSize threadGroupSize = MTLSizeMake(tgSize, 1, 1);
      [computeEncoder dispatchThreadgroups:threadgroupsPerGrid threadsPerThreadgroup:threadGroupSize];

      getMPSProfiler().endProfileKernel(binaryPSO);
    }
  });
  return grad_input;
}

} // namespace

TORCH_LIBRARY_IMPL(torchvision, MPS, m) {
  m.impl(
      TORCH_SELECTIVE_NAME("torchvision::ps_roi_pool"),
      TORCH_FN(ps_roi_pool_forward_kernel));
  m.impl(
      TORCH_SELECTIVE_NAME("torchvision::_ps_roi_pool_backward"),
      TORCH_FN(ps_roi_pool_backward_kernel));
}

} // namespace ops
} // namespace vision
