import pytest
import torch
import torch.fx
import torchvision.prototype.models.depth.stereo.raft_stereo as raft_stereo
from common_utils import set_rng_seed, cpu_and_gpu


@pytest.mark.parametrize("model_builder", (raft_stereo.raft_stereo, raft_stereo.raft_stereo_fast))
@pytest.mark.parametrize("model_mode", ("standard", "scripted", "fx"))
@pytest.mark.parametrize("dev", cpu_and_gpu())
def test_raft_stereo(model_builder, model_mode, dev):
    # A simple test to make sure the model can do forward pass, jit scriptable, and fx traceable
    set_rng_seed(0)

    model = model_builder().eval().to(dev)
    num_iters = model.num_iters
    if model_mode == "standard":
        model = torch.jit.script(model)
    elif model_mode == "fx":
        model = torch.fx.symbolic_trace(model)

    img1 = torch.rand(1, 3, 64, 64).to(dev)
    img2 = torch.rand(1, 3, 64, 64).to(dev)

    preds = model(img1, img2)

    assert len(preds) == num_iters, "Number of predictions should be the same as model.num_iters"

    assert preds[0].shape == torch.Size(
        [1, 1, 64, 64]
    ), f"The output shape of preds[0] should be [1, 1, 64, 64] but instead it is {preds[0].shape}"
