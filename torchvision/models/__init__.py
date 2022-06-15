from .alexnet import *
from .convnext import *
from .densenet import *
from .efficientnet import *
from .googlenet import *
from .inception import *
from .mnasnet import *
from .mobilenet import *
from .regnet import *
from .resnet import *
from .shufflenetv2 import *
from .squeezenet import *
from .vgg import *
from .vision_transformer import *
from .swin_transformer import *
from . import optical_flow
from . import quantization
from . import segmentation
from . import video
from ._api import get_weight

_BETA_DETECTION_IS_ENABLED = False


def enable_beta_detection():
    global _BETA_DETECTION_IS_ENABLED
    _BETA_DETECTION_IS_ENABLED = True
