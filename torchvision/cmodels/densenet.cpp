#include "densenet.h"

torchvision::densenetimpl::_DenseLayerImpl::_DenseLayerImpl(
	int64_t num_input_features, int64_t growth_rate, int64_t bn_size,
	double drop_rate)
	: drop_rate(drop_rate)
{
	push_back(torch::nn::BatchNorm(num_input_features));
	push_back(visionimpl::Relu(true));
	push_back(torch::nn::Conv2d(
		torch::nn::Conv2dOptions(num_input_features, bn_size * growth_rate, 1)
			.stride(1)
			.with_bias(false)));
	push_back(torch::nn::BatchNorm(bn_size * growth_rate));
	push_back(visionimpl::Relu(true));
	push_back(torch::nn::Conv2d(
		torch::nn::Conv2dOptions(bn_size * growth_rate, growth_rate, 3)
			.stride(1)
			.padding(1)
			.with_bias(false)));
}

torch::Tensor torchvision::densenetimpl::_DenseLayerImpl::forward(at::Tensor x)
{
	auto new_features = torch::nn::SequentialImpl::forward(x);
	if (drop_rate > 0)
		new_features =
			torch::dropout(new_features, drop_rate, this->is_training());
	return torch::cat({x, new_features}, 1);
}

torchvision::densenetimpl::_DenseBlockImpl::_DenseBlockImpl(
	int64_t num_layers, int64_t num_input_features, int64_t bn_size,
	int64_t growth_rate, double drop_rate)
{
	for (int64_t i = 0; i < num_layers; ++i)
	{
		auto layer = _DenseLayer(num_input_features + i * growth_rate,
								 growth_rate, bn_size, drop_rate);
		push_back(layer);
	}
}

torch::Tensor torchvision::densenetimpl::_DenseBlockImpl::forward(at::Tensor x)
{
	return torch::nn::SequentialImpl::forward(x);
}

torchvision::densenetimpl::_TransitionImpl::_TransitionImpl(
	int64_t num_input_features, int64_t num_output_features)
{
	push_back(torch::nn::BatchNorm(num_input_features));
	push_back(visionimpl::Relu(true));
	push_back(torch::nn::Conv2d(
		torch::nn::Conv2dOptions(num_input_features, num_output_features, 1)
			.stride(1)
			.with_bias(false)));
	push_back(visionimpl::AvgPool2D(torch::IntList({2}), torch::IntList({2})));
}

torch::Tensor torchvision::densenetimpl::_TransitionImpl::forward(at::Tensor x)
{
	return torch::nn::SequentialImpl::forward(x);
}

torchvision::DenseNetImpl::DenseNetImpl(int64_t growth_rate,
										std::vector<int64_t> block_config,
										int64_t num_init_features,
										int64_t bn_size, int64_t drop_rate,
										int64_t num_classes)
{
	features = torch::nn::Sequential(
		torch::nn::Conv2d(torch::nn::Conv2dOptions(3, num_init_features, 7)
							  .stride(2)
							  .padding(3)
							  .with_bias(false)),
		torch::nn::BatchNorm(num_init_features), visionimpl::Relu(true),
		visionimpl::MaxPool2D(3, 2, false, torch::IntList({1})));

	auto num_features = num_init_features;
	for (size_t i = 0; i < block_config.size(); ++i)
	{
		auto num_layers = block_config[i];
		auto block = densenetimpl::_DenseBlock(num_layers, num_features,
											   bn_size, growth_rate, drop_rate);
		features->push_back(block);
		num_features = num_features + num_layers * growth_rate;

		if (i != block_config.size() - 1)
		{
			auto trans =
				densenetimpl::_Transition(num_features, num_features / 2);
			features->push_back(trans);
			num_features = num_features / 2;
		}
	}

	features->push_back(torch::nn::BatchNorm(num_features));
	classifier = torch::nn::Linear(num_features, num_classes);

	for (auto &module : modules(false))
	{
		if (torch::nn::Conv2dImpl *M =
				dynamic_cast<torch::nn::Conv2dImpl *>(module.get()))
			torch::nn::init::xavier_normal_(M->weight);
		else if (torch::nn::BatchNormImpl *M =
					 dynamic_cast<torch::nn::BatchNormImpl *>(module.get()))
		{
			torch::nn::init::constant_(M->weight, 1);
			torch::nn::init::constant_(M->bias, 0);
		}
		else if (torch::nn::LinearImpl *M =
					 dynamic_cast<torch::nn::LinearImpl *>(module.get()))
			torch::nn::init::normal_(M->bias, 0);
	}

	register_module("features", features);
	register_module("classifier", classifier);
}

torch::Tensor torchvision::DenseNetImpl::forward(at::Tensor x)
{
	auto features = this->features->forward(x);
	auto out = torch::relu_(features);
	out = torch::adaptive_avg_pool2d(out, {1, 1}).view({features.size(0), -1});
	out = this->classifier->forward(out);
	return out;
}

torchvision::DenseNet121Impl::DenseNet121Impl()
	: DenseNetImpl(32, {6, 12, 32, 32}, 64)
{
}

torchvision::DenseNet169Impl::DenseNet169Impl()
	: DenseNetImpl(32, {6, 12, 32, 32}, 64)
{
}

torchvision::DenseNet201Impl::DenseNet201Impl()
	: DenseNetImpl(32, {6, 12, 48, 32}, 64)
{
}

torchvision::DenseNet161Impl::DenseNet161Impl()
	: DenseNetImpl(48, {6, 12, 36, 24}, 96)
{
}
