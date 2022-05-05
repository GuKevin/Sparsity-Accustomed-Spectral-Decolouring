import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset
from torch import nn
import matplotlib.pyplot as plt
from sklearn.preprocessing import minmax_scale
import itertools
import h5py
from matplotlib.colors import LinearSegmentedColormap

cmap = LinearSegmentedColormap.from_list('rb',['b','w','r'], N=256)
'''
def spectrum_normalisation(spectrum):
# Applies (-1,1) min-max scaling to the initial pressure spectrum
    norm = minmax_scale(spectrum,feature_range=(-1,1))
    return norm
'''
def timestep_preprocessing(timebatch):
    processed = []
    for spectrum in timebatch:
        processed.append(torch.tensor(spectrum_normalisation(list(spectrum))))
    return torch.stack(processed)

def spectrum_normalisation(spectrum):
    # Applies z-score scaling to the initial pressure spectrum
    mean = np.mean(spectrum)
    std = np.std(spectrum)
    norm = (spectrum - mean)/std
    return norm


def spectrum_processing(spectrum,allowed_indices):
# Takes in the full 41-long spectrum, and returns the normalised incomplete spectrum
    temp = []
    for i in range(len(spectrum)):
        if i in allowed_indices:
            temp.append(spectrum[i])
    temp = spectrum_normalisation(temp)
    return temp

def batch_processing(batch,allowed_indices):
# Returns incomplete + normalised initial pressure spectra from the original dataset
    processed = []
    for spectrum in batch:
        processed.append(spectrum_processing(spectrum, allowed_indices))

    return torch.tensor(np.array(processed))

def testset_error_fraction(y_true, y_pred):
# Function for finding the median and IQR for so2 error
    error = abs((y_true - y_pred) / y_true)
    q = torch.tensor([0.25,0.50,0.75])
    IQR = torch.quantile(error,q)
    return IQR

# Importing the full initial pressure spectra
train_spectra_original = torch.load('../Datasets/NoSkin_filtered/filtered_training_spectra.pt')
validation_spectra_original = torch.load('../Datasets/NoSkin_filtered/filtered_validation_spectra.pt')
test_spectra_original = torch.load('../Datasets/NoSkin_filtered/filtered_test_spectra.pt')

print(train_spectra_original[53])

# Importing the ground truth oxygenations, and reshaping so that each spectrum has a label
train_oxygenations = torch.load('../Datasets/NoSkin_filtered/filtered_training_oxygenations.pt')
validation_oxygenations = torch.load('../Datasets/NoSkin_filtered/filtered_validation_oxygenations.pt')
test_oxygenations = torch.load('../Datasets/NoSkin_filtered/filtered_test_oxygenations.pt')
train_oxygenations= torch.reshape(train_oxygenations,(len(train_oxygenations),1))
validation_oxygenations = torch.reshape(validation_oxygenations,(len(validation_oxygenations),1))
test_oxygenations=torch.reshape(test_oxygenations,(len(test_oxygenations),1))
test_oxygenations = np.float32(test_oxygenations)
test_oxygenations = torch.tensor(test_oxygenations)

# Removing some wavelength data depending on the number of datapoints the network will be trained on
N_datapoints = 10


indices_10 = [0,6,10,12,14,20,24,28,30, 36]
if N_datapoints == 10:
    allowed_datapoints = indices_10

train_spectra = batch_processing(train_spectra_original,allowed_datapoints)
validation_spectra = batch_processing(validation_spectra_original,allowed_datapoints)
test_spectra = batch_processing(test_spectra_original,allowed_datapoints)

print(train_spectra[53])

# Initialising dataloaders
train_ds = TensorDataset(train_spectra,train_oxygenations)
validation_ds = TensorDataset(validation_spectra,validation_oxygenations)

batch_size = 1024

train_loader = DataLoader(train_ds, batch_size, shuffle=True)
valid_loader = DataLoader(validation_ds, batch_size)
print('Data imported and loaded')

# Defining LSD network
class LSD(nn.Module):

    def __init__(self):
        super().__init__()

        self.LSD = nn.Sequential(
            nn.LeakyReLU(),
            nn.Linear(in_features=N_datapoints,out_features=N_datapoints*2),
            nn.LeakyReLU(),
            nn.Linear(in_features=N_datapoints*2, out_features=N_datapoints*2),
            nn.LeakyReLU(),
            nn.Linear(in_features=N_datapoints*2, out_features=N_datapoints*2),
            nn.LeakyReLU(),
            nn.Linear(in_features=N_datapoints*2, out_features=1)
        )

    def forward(self, x):
        x = self.LSD(x)
        return x

### Define the loss function
loss_fn = torch.nn.L1Loss()

### Set the random seed for reproducible results
torch.manual_seed(0)

### Initialize the network
network = LSD()

params_to_optimize = [
    {'params': network.parameters()}
]

# Check if the GPU is available
device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
print(f'Selected device: {device}')

# Move the network to the selected device
network.to(device)

### Training function
def train_epoch_den(network, device, dataloader, loss_fn, optimizer):
    network.train()
    train_loss = []
    # Iterate the dataloader
    for batch in dataloader:
        spectrum_batch = batch[0]
        labels = batch[1]
        # Move tensor to the proper device
        spectrum_batch = spectrum_batch.to(device)
        labels = labels.to(device)
        output_data = network(spectrum_batch.float())
        # Evaluate loss
        loss = loss_fn(output_data.float(), labels.float())
        # Backward pass
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        # Print batch loss
        # print('\t partial train loss (single batch): %f' % (loss.data))
        train_loss.append(loss.detach().cpu().numpy())

    return np.mean(train_loss)

### Testing function
def test_epoch_den(network, device, dataloader, loss_fn):
    # Set evaluation mode for encoder and decoder
    network.eval()
    with torch.no_grad():  # No need to track the gradients
        # Define the lists to store the outputs for each batch
        conc_out = []
        conc_label = []
        i = 0
        for batch in dataloader:
            spectrum_batch = batch[0]
            labels = batch[1]
            # Move tensor to the proper device
            spectrum_batch = spectrum_batch.to(device)
            labels = labels.to(device)
            output_data = network(spectrum_batch.float())

            if i == 0 and flag == True:
                print(labels[0])
                print('label printed')
                print(output_data[0])
                print('output printed')
            i += 1

            # Append the network output and the original to the lists
            conc_out.append(output_data.cpu())
            conc_label.append(labels.cpu())
        # Create a single tensor with all the values in the lists
        conc_out = torch.cat(conc_out)
        conc_label = torch.cat(conc_label)

        conc_error_fractions = abs((conc_out - conc_label) / conc_label)

        # Evaluate global loss
        val_loss = loss_fn(conc_out, conc_label)
    return val_loss.data, torch.median(conc_error_fractions)

### Training cycle
num_epochs = 100
history_da = {'train_loss': [], 'val_loss': []}
flag = True

for epoch in range(num_epochs):
    print('EPOCH %d/%d' % (epoch + 1, num_epochs))

    if epoch % 2 == 1:
        lr = 0.01 * 0.9 ** ((epoch - 1) / 2)
    else:
        lr = 0.01 * 0.9 ** (epoch / 2)

    optim = torch.optim.Adam(params_to_optimize, lr=lr)

    ### Training (use the training function)
    train_loss = train_epoch_den(
        network=network,
        device=device,
        dataloader=train_loader,
        loss_fn=loss_fn,
        optimizer=optim)
    print('Training done')

    ### Validation  (use the testing function)
    val_loss, median_error = test_epoch_den(
        network=network,
        device=device,
        dataloader=valid_loader,
        loss_fn=loss_fn)

    # Print Validationloss
    history_da['train_loss'].append(train_loss)
    history_da['val_loss'].append(val_loss)
    print('\n EPOCH {}/{} \t train loss {:.3f} \t val loss {:.3f}'.format(epoch + 1, num_epochs, train_loss, val_loss))
    print('Median error fraction: ' + str(float(median_error)))


# Evaluate performance on test set
network.eval()
test_spectra = test_spectra.to(device)
predictions = network(test_spectra.float())
print(predictions)
print(type(predictions))
print(test_oxygenations)
print(type(test_oxygenations))
IQR = testset_error_fraction(test_oxygenations,predictions)
print(IQR)
########################################################################################################################
#Full mouse Gas Challenge evaluation

# Data for the PA reconstruction
f = h5py.File('I:/research/seblab/data/group_folders/Janek/kevindata/Scan_108.hdf5','r')
reconstructed_data = f['recons']['Backprojection Preclinical']['0']

timesteps = 93
measured_wavelengths = [700, 730, 750, 760, 770, 800, 820, 840, 850, 880]

# Plotting the segmentation lines for the tumour and reference region
s1 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines1.npy')
s2 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines2.npy')
s3 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines3.npy')
s4 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines4.npy')
s5 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines5.npy')
s6 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines6.npy')
s7 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines7.npy')
s8 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines8.npy')
s9 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines9.npy')
s10 = np.load('../Datasets/GasChallengeFullMouse/Segmentation/Outlines10.npy')
# Collecting the coordinates of the points within the segmented regions
chosen_pixel_coords = np.load('../Datasets/GasChallengeFullMouse/Segmentation/pixel_coords.npy')
chosen_pixel_coords = list(chosen_pixel_coords)
chosen_pixel_coords = [list(entry) for entry in chosen_pixel_coords]
tumour_coords = np.load('../Datasets/GasChallengeFullMouse/Segmentation/tumourcoords.npy')
tumour_coords = list(tumour_coords)
tumour_coords = [list(entry) for entry in tumour_coords]

tumour_indices = []
for i in range(len(tumour_coords)):
    tumour_indices.append(chosen_pixel_coords.index(tumour_coords[i]))

# For each timestep, the model estimates so2 for each pixel:
so2_timeseries_bypixel = [[] for i in range(len(chosen_pixel_coords))]

for i in range(timesteps):
    print(i)
    filename = '../Datasets/GasChallengeFullMouse/Timestep' + str(i) + '.pt'
    data = torch.load(filename)
    data = timestep_preprocessing(data)
    # MODEL SO2 ESTIMATION ON EACH PIXEL'S SPECTRUM
    invitro_predictions = network(data.float())
    for j in range(len(invitro_predictions)):
        so2_timeseries_bypixel[j].append(np.float64(invitro_predictions[j]))

#np.save('../GasChallengeFullMouse_results/LSD/so2_timeseries_bypixel.npy', np.array(so2_timeseries_bypixel))

'''
# Plotting the results
for i in range(timesteps):
    background_image_data = reconstructed_data[i][5] # Background greyscale PA image at 800nm
    plt.imshow(background_image_data,cmap='gray',origin = 'lower')
    so2_data_fortimestep = [pixel[i] for pixel in so2_timeseries_bypixel]
    so2_map = [[np.NaN for i in range(250)] for j in range(250)]

    count = 0
    for entry in chosen_pixel_coords:
        x_coord = entry[0]
        y_coord = entry[1]
        so2_map[y_coord][x_coord] = so2_data_fortimestep[count]*100
        count += 1

    plt.imshow(so2_map,interpolation = 'nearest', vmin = 0, vmax = 100, origin = 'lower')
    clb = plt.colorbar()
    clb.ax.set_title('sO$_2$ [%]')
    plt.plot(*zip(*s1), color='r')
    plt.plot(*zip(*s2), color='r')
    plt.plot(*zip(*s3), color='r')
    plt.plot(*zip(*s4), color='r')
    plt.plot(*zip(*s5), color='r')
    plt.plot(*zip(*s6), color='r')
    plt.plot(*zip(*s7), color='r')
    plt.plot(*zip(*s8), color='r')
    plt.plot(*zip(*s9), color='r')
    plt.plot(*zip(*s10), color='r')
    file = '../GasChallengeFullMouse_results/LSD/Absoluteso2maps/GasChallengeFullMouse_Timestep' + str(i)
    #plt.savefig(file)
    plt.show()
'''
# Now implementing a 'baseline so2 comparison'
N_baseline_timesteps = 10
pixel_baselines = []
for i in range(len(so2_timeseries_bypixel)):
    baseline = np.mean(so2_timeseries_bypixel[i][0:N_baseline_timesteps])
    pixel_baselines.append(baseline)

delta_so2_timeseries_bypixel = []
for i in range(len(so2_timeseries_bypixel)):
    delta_so2_series = list(np.array(so2_timeseries_bypixel[i]) - pixel_baselines[i])
    delta_so2_series = delta_so2_series[N_baseline_timesteps:]
    delta_so2_timeseries_bypixel.append(delta_so2_series)

#np.save('../GasChallengeFullMouse_results/LSD/Baseline10_delta_so2_timeseries_bypixel.npy', np.array(delta_so2_timeseries_bypixel))

'''
# Plotting the delta_so2_maps
for i in range(timesteps-N_baseline_timesteps):
    background_image_data = reconstructed_data[i+N_baseline_timesteps][5] # Background greyscale PA image at 800nm


    delta_so2_data_fortimestep = [pixel[i] for pixel in delta_so2_timeseries_bypixel]

    delta_so2_map = [[np.NaN for i in range(250)] for j in range(250)]
    print(min(delta_so2_data_fortimestep))
    print(max(delta_so2_data_fortimestep))
    count = 0
    for entry in chosen_pixel_coords:
        x_coord = entry[0]
        y_coord = entry[1]
        delta_so2_map[y_coord][x_coord] = delta_so2_data_fortimestep[count]*100
        count += 1
    fig,axes = plt.subplots()
    plt.imshow(background_image_data, cmap='gray', origin='lower')
    plt.imshow(delta_so2_map,interpolation = 'nearest', vmin = -70, vmax = 70, origin = 'lower', cmap=cmap)
    clb = plt.colorbar()
    clb.ax.set_title('\u0394sO$_2$ [%]')
    plt.plot(*zip(*s1), color='r')
    plt.plot(*zip(*s2), color='r')
    plt.plot(*zip(*s3), color='r')
    plt.plot(*zip(*s4), color='r')
    plt.plot(*zip(*s5), color='r')
    plt.plot(*zip(*s6), color='r')
    plt.plot(*zip(*s7), color='r')
    plt.plot(*zip(*s8), color='r')
    plt.plot(*zip(*s9), color='r')
    plt.plot(*zip(*s10), color='r')
    file = '../GasChallengeFullMouse_results/LSD/Deltaso2maps/GasChallengeFullMouse_deltaso2_baseline10_Timestep' + str(i+N_baseline_timesteps)
    #plt.savefig(file)
    plt.show()
'''
time_averaged_delta_so2_bypixel =  [np.mean(entry) for entry in delta_so2_timeseries_bypixel]
time_averaged_absolute_delta_so2_bypixel = [np.mean(abs(np.array(entry))) for entry in delta_so2_timeseries_bypixel]

time_averaged_delta_so2_tumourpixels = []
time_averaged_delta_so2_nontumourpixels = []
time_averaged_absolute_delta_so2_tumourpixels = []
time_averaged_absolute_delta_so2_nontumourpixels = []
for i in range(len(time_averaged_absolute_delta_so2_bypixel)):
    if i in tumour_indices:
        time_averaged_absolute_delta_so2_tumourpixels.append(time_averaged_absolute_delta_so2_bypixel[i])
        time_averaged_delta_so2_tumourpixels.append(time_averaged_delta_so2_bypixel[i])
    else:
        time_averaged_absolute_delta_so2_nontumourpixels.append(time_averaged_absolute_delta_so2_bypixel[i])
        time_averaged_delta_so2_nontumourpixels.append(time_averaged_delta_so2_bypixel[i])

plt.hist(np.array(time_averaged_delta_so2_tumourpixels)*100, bins = 100, range = [-30,30], facecolor = 'gray', align = 'mid')
plt.xlabel('Time-averaged \u0394sO$_2$ from baseline [%] ')
plt.ylabel('Frequency')
#plt.savefig('../GasChallengeFullMouse_results/LSD/Baseline10_TumourHistogram0.png')
plt.show()

plt.hist(np.array(time_averaged_delta_so2_nontumourpixels) * 100, bins=100, range=[-30, 30], facecolor='gray', align='mid')
plt.xlabel('Time-averaged \u0394sO$_2$ from baseline [%] ')
plt.ylabel('Frequency')
# plt.savefig('../GasChallengeFullMouse_results/LSD/Baseline10_NonTumourHistogram0.png')
plt.show()
plt.hist(np.array(time_averaged_absolute_delta_so2_tumourpixels)*100, bins = 100, range = [0,30], facecolor = 'gray', align = 'mid')
plt.xlabel('Time-averaged |\u0394sO$_2$| from baseline [%] ')
plt.ylabel('Frequency')
#plt.savefig('../GasChallengeFullMouse_results/LSD/Baseline10_TumourHistogram.png')
plt.show()
plt.hist(np.array(time_averaged_absolute_delta_so2_nontumourpixels)*100, bins = 100, range = [0,30], facecolor = 'gray', align = 'mid')
plt.xlabel('Time-averaged |\u0394sO$_2$| from baseline [%] ')
plt.ylabel('Frequency')
#plt.savefig('../GasChallengeFullMouse_results/LSD/Baseline10_NonTumourHistogram.png')
plt.show()