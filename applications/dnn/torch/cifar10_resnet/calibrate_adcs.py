"""
Script to obtain calibrated ADC ranges for CIFAR-10 ResNets.
Run this after calibrating crossbar inputs!
"""

import torch
from torchvision import datasets, transforms
import numpy as np
import warnings, sys, time, os
from build_resnet_cifar10 import ResNet_cifar10
warnings.filterwarnings('ignore')
#sys.path.append("../../") # to import dnn_inference_params
#sys.path.append("../../../../") # to import simulator
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),"../../"))) # to import dnn_inference_params
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),"../../../../"))) # to import simulator
from simulator import CrossSimParameters
from simulator.algorithms.dnn.torch.convert import from_torch, convertible_modules, analog_modules, reinitialize
from simulator.algorithms.dnn.torch.profile import get_profiled_adc_inputs
from dnn_inference_params import dnn_inference_params
from calibration import calibrate_adc_limits

## Depth parameter for model selection
# Follows definition in original ResNet paper (He et al, CVPR 2016)
# n = 2 : ResNet-14 (175K weights)
# n = 3 : ResNet-20 (272K weights)
# n = 5 : ResNet-32 (467K weights)
# n = 9 : ResNet-56 (856K weights)
n = 3

useGPU = True # use GPU?
N = 500 # number of images from the TRAINING set
batch_size = 32
Nruns = 1
print_progress = True

depth = 6*n+2
print("Model: ResNet-{:d}".format(depth))
print("CIFAR-10: using "+("GPU" if useGPU else "CPU"))
print("Number of images: {:d}".format(N))
print("Number of runs: {:d}".format(Nruns))
print("Batch size: {:d}".format(batch_size))
device = torch.device("cuda:0" if (torch.cuda.is_available() and useGPU) else "cpu")

##### Load Pytorch model
resnet_model = ResNet_cifar10(n)
resnet_model = resnet_model.to(device)
#resnet_model.load_state_dict(
#    torch.load('./models/resnet{:d}_cifar10.pth'.format(depth),
#    map_location=torch.device(device)))
model_path = os.path.join(os.path.dirname(__file__), 'models','resnet{:d}_cifar10.pth'.format(depth))
resnet_model.load_state_dict(
      torch.load(model_path, map_location=torch.device(device)))
resnet_model.eval()
n_layers = len(convertible_modules(resnet_model))

##### Set the simulation parameters

# Create a list of CrossSimParameters objects
params_list = [None] * n_layers

# Params arguments common to all layers
base_params_args = {
    'ideal' : False,
    ## Mapping style
    'core_style' : "BALANCED",
    'Nslices' : 1,
    ## Weight value representation and precision
    'weight_bits' : 8,
    'weight_percentile' : 100,
    'digital_bias' : True,
    ## Memory device
    'Rmin' : 1e4,
    'Rmax' : 1e6,
    'infinite_on_off_ratio' : True,
    'error_model' : "none",
    'alpha_error' : 0.0,
    'proportional_error' : False,
    'noise_model' : "none",
    'alpha_noise' : 0.0,
    'proportional_noise' : False,
    'drift_model' : "none",
    't_drift' : 0,
    ## Array properties
    'NrowsMax' : 1152,
    'NcolsMax' : None,
    'Rp_row' : 0, # ohms
    'Rp_col' : 0, # ohms
    'interleaved_posneg' : False,
    'subtract_current_in_xbar' : True,
    'current_from_input' : True,
    ## Input quantization
    'input_bits' : 8,
    'input_bitslicing' : False,
    'input_slice_size' : 1,
    ## ADC
    'adc_bits' : 0,
    'adc_range_option' : "CALIBRATED",
    'adc_type' : "generic",
    ## Simulation parameters
    'useGPU' : useGPU,
    ## Profiling
    'profile_xbar_inputs' : False,
    'profile_adc_inputs' : True,
    'ntest' : N,
    }

### Load input limits
input_ranges = np.load(os.path.join(os.path.dirname(__file__),'calibrated_config/input_limits_ResNet{:d}.npy').format(depth))

### Set the parameters
for k in range(n_layers):
    params_args_k = base_params_args.copy()
    params_args_k['positiveInputsOnly'] = (False if k == 0 else True)
    params_args_k['input_range'] = input_ranges[k]
    params_list[k] = dnn_inference_params(**params_args_k)

#### Convert PyTorch layers to analog layers
analog_resnet = from_torch(resnet_model, params_list, fuse_batchnorm=True, bias_rows=0)

#### Load and transform CIFAR-10 dataset
normalize = transforms.Normalize(
    mean = [0.485, 0.456, 0.406],
    std  = [0.229, 0.224, 0.225])
dataset = datasets.CIFAR10(root='./',train=True, download=True, 
    transform= transforms.Compose([transforms.ToTensor(), normalize]))
dataset = torch.utils.data.Subset(dataset, np.arange(N))
cifar10_dataloader = torch.utils.data.DataLoader(dataset, batch_size=batch_size)

#### Run inference and evaluate accuracy
T1 = time.time()
y_pred, y, k = np.zeros(N), np.zeros(N), 0
for inputs, labels in cifar10_dataloader:
    inputs = inputs.to(device)
    output = analog_resnet(inputs)
    output = output.to(device)
    y_pred_k = output.data.cpu().detach().numpy()
    if batch_size == 1:
        y_pred[k] = y_pred_k.argmax()
        y[k] = labels.cpu().detach().numpy()
        k += 1
    else:
        batch_size_k = y_pred_k.shape[0]
        y_pred[k:(k+batch_size_k)] = y_pred_k.argmax(axis=1)
        y[k:(k+batch_size_k)] = labels.cpu().detach().numpy()
        k += batch_size_k
    if print_progress:
        print("Image {:d}/{:d}, accuracy so far = {:.2f}%".format(
            k, N, 100*np.sum(y[:k] == y_pred[:k])/k), end="\r")

T2 = time.time()
top1 = np.sum(y == y_pred)/len(y)
print("\nInference finished. Elapsed time: {:.3f} sec".format(T2-T1))
print('Accuracy: {:.2f}% ({:d}/{:d})\n'.format(top1*100,int(top1*N),N))

#### Retrieve profiled inputs and calibrate limits
print("Collecting profiled ADC data")
profiled_adc_inputs = get_profiled_adc_inputs(analog_resnet)
print("Optimizing ADC limits")
calibrated_adc_ranges = calibrate_adc_limits(
    analog_modules(analog_resnet), profiled_adc_inputs, Nbits=8)

## Make sure the file name matches the parameters used!!
# np.save("./calibrated_config/adc_limits_ResNet{:d}_balanced.npy".format(depth),
    # calibrated_adc_ranges)
