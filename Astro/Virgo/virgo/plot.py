import os
import sys
import argparse
import time
import numpy as np
import datetime
import shutil
import warnings
def plot(obs_parameters='', n=0, m=0, f_rest=0, slope_correction=False, dB=False, rfi=[0,0], xlim=[0,0], ylim=[0,0], dm=0,
	 obs_file='observation.dat', cal_file='', waterfall_fits='', spectra_csv='', power_csv='', plot_file='plot.png'):
	import matplotlib
	matplotlib.use('Agg') # Try commenting this line if you run into display/rendering errors
	import matplotlib.pyplot as plt
	from matplotlib.gridspec import GridSpec

	plt.rcParams['legend.fontsize'] = 14
	plt.rcParams['axes.labelsize'] = 14
	plt.rcParams['axes.titlesize'] = 18
	plt.rcParams['xtick.labelsize'] = 12
	plt.rcParams['ytick.labelsize'] = 12

	def decibel(x):
		if dB: return 10.0*np.log10(x)
		return x

	def shift(phase_num, n_rows):
		waterfall[:, phase_num] = np.roll(waterfall[:, phase_num], -n_rows)

	def SNR(spectrum, mask=np.array([])):
		'''Signal-to-Noise Ratio estimator, with optional masking.
		If mask not given, then all channels will be used to estimate noise
		(will drastically underestimate S:N - not robust to outliers!)'''

		if mask.size == 0:
			mask = np.zeros_like(spectrum)

		noise = np.nanstd((spectrum[2:]-spectrum[:-2])[mask[1:-1] == 0])/np.sqrt(2)
		background = np.nanmean(spectrum[mask == 0])

		return (spectrum-background)/noise

	def best_fit(power):
		'''Compute best Gaussian fit'''
		avg = np.nanmean(power)
		var = np.var(power)

		gaussian_fit_x = np.linspace(np.min(power),np.max(power),100)
		gaussian_fit_y = 1.0/np.sqrt(2*np.pi*var)*np.exp(-0.5*(gaussian_fit_x-avg)**2/var)

		return [gaussian_fit_x, gaussian_fit_y]

	# Load observation parameters from dictionary argument/header file
	if obs_parameters != '':
		frequency = obs_parameters['frequency']
		bandwidth = obs_parameters['bandwidth']
		channels = obs_parameters['channels']
		t_sample = obs_parameters['t_sample']
	else:
		header_file = '.'.join(obs_file.split('.')[:-1])+'.header'

		warnings.warn('No observation parameters passed. Attempting to load from header file ('+header_file+')...')

		with open(header_file, 'r') as f:
			headers = [parameter.rstrip('\n') for parameter in f.readlines()]

		for i in range(len(headers)):
			if 'mjd' in headers[i]:
				mjd = float(headers[i].strip().split('=')[1])
			elif 'frequency' in headers[i]:
				frequency = float(headers[i].strip().split('=')[1])
			elif 'bandwidth' in headers[i]:
				bandwidth = float(headers[i].strip().split('=')[1])
			elif 'channels' in headers[i]:
				channels = int(headers[i].strip().split('=')[1])
			elif 't_sample' in headers[i]:
				t_sample = float(headers[i].strip().split('=')[1])

	# Transform frequency axis limits to MHz
	xlim = [x / 1e6 for x in xlim]

	# Define Radial Velocity axis limits
	left_velocity_edge = -299792.458*(bandwidth-2*frequency+2*f_rest)/(bandwidth-2*frequency)
	right_velocity_edge = 299792.458*(-bandwidth-2*frequency+2*f_rest)/(bandwidth+2*frequency)

	# Transform sampling time to number of bins
	bins = int(t_sample*bandwidth/channels)

	# Load observation & calibration data
	offset = 1
	waterfall = offset*np.fromfile(obs_file, dtype='float32').reshape(-1, channels)/bins

	# Delete first 3 rows (potentially containing outlier samples)
	waterfall = waterfall[3:, :]

	# Mask RFI-contaminated channels
	if rfi != [0,0]:
		# Frequency to channel transformation
		rfi_lo = channels*(rfi[0] - (frequency - bandwidth/2))/bandwidth
		rfi_hi = channels*(rfi[1] - (frequency - bandwidth/2))/bandwidth

		# Blank channels
		for i in range(int(rfi_lo), int(rfi_hi)):
			waterfall[:, i] = np.nan

	if cal_file != '':
		waterfall_cal = offset*np.fromfile(cal_file, dtype='float32').reshape(-1, channels)/bins

		# Delete first 3 rows (potentially containing outlier samples)
		waterfall_cal = waterfall_cal[3:, :]

		# Mask RFI-contaminated channels
		if rfi != [0,0]:
			# Blank channels
			for i in range(int(rfi_lo), int(rfi_hi)):
				waterfall_cal[:, i] = np.nan

	# Compute average spectra
	with warnings.catch_warnings():
		warnings.filterwarnings(action='ignore', message='Mean of empty slice')
		avg_spectrum = decibel(np.nanmean(waterfall, axis=0))
		if cal_file != '':
			avg_spectrum_cal = decibel(np.nanmean(waterfall_cal, axis=0))

	# Number of sub-integrations
	subs = waterfall.shape[0]

	# Compute Time axis
	t = t_sample*np.arange(subs)

	# Compute Frequency axis; convert Hz to MHz
	frequency = np.linspace(frequency-0.5*bandwidth, frequency+0.5*bandwidth,
	                        channels, endpoint=False)*1e-6

	# Perform de-dispersion
	if dm != 0:
		deltaF = float(np.max(frequency)-np.min(frequency))/subs
		f_start = np.min(frequency)
		for t_bin in range(subs):
			f_chan = f_start+t_bin*deltaF
			deltaT = 4149*dm*((1/(f_chan**2))-(1/(np.max(frequency)**2)))
			n = int((float(deltaT)/(float(1)/channels)))
			shift(t_bin, n)

	# Define array for Time Series plot
	power = decibel(np.nanmean(waterfall, axis=1))

	# Apply Mask
	mask = np.zeros_like(avg_spectrum)
	mask[np.logical_and(frequency > f_rest*1e-6-0.2, frequency < f_rest*1e-6+0.8)] = 1 # Margins OK for galactic HI

	# Define text offset for axvline text label
	text_offset = 0

	# Calibrate Spectrum
	if cal_file != '':
		if dB:
			spectrum = 10**((avg_spectrum-avg_spectrum_cal)/10)
		else:
			spectrum = avg_spectrum/avg_spectrum_cal

		spectrum = SNR(spectrum, mask)
		if slope_correction:
			idx = np.isfinite(frequency) & np.isfinite(spectrum)
			fit = np.polyfit(frequency[idx], spectrum[idx], 1)
			ang_coeff = fit[0]
			intercept = fit[1]
			fit_eq = ang_coeff*frequency + intercept
			spectrum = SNR(spectrum-fit_eq, mask)

		# Mitigate RFI (Frequency Domain)
		if n != 0:
			spectrum_clean = SNR(spectrum.copy(), mask)
			for i in range(0, int(channels)):
				spectrum_clean[i] = np.nanmedian(spectrum_clean[i:i+n])

		# Apply position offset for Spectral Line label
		text_offset = 60

	# Mitigate RFI (Time Domain)
	if m != 0:
		power_clean = power.copy()
		for i in range(0, int(subs)):
			power_clean[i] = np.nanmedian(power_clean[i:i+m])

	# Write Waterfall to file (FITS)
	if waterfall_fits != '':
		from astropy.io import fits

		# Load data
		hdu = fits.PrimaryHDU(waterfall)

		# Prepare FITS headers
		hdu.header['NAXIS'] = 2
		hdu.header['NAXIS1'] = channels
		hdu.header['NAXIS2'] = subs
		hdu.header['CRPIX1'] = channels/2
		hdu.header['CRPIX2'] = subs/2
		hdu.header['CRVAL1'] = frequency[channels/2]
		hdu.header['CRVAL2'] = t[subs/2]
		hdu.header['CDELT1'] = bandwidth*1e-6/channels
		hdu.header['CDELT2'] = t_sample
		hdu.header['CTYPE1'] = 'Frequency (MHz)'
		hdu.header['CTYPE2'] = 'Relative Time (s)'
		try:
			hdu.header['MJD-OBS'] = mjd
		except NameError:
			warnings.warn('Observation MJD could not be found and will not be part of the FITS header.')
			pass

		# Delete pre-existing FITS file
		try:
			os.remove(waterfall_fits)
		except OSError:
			pass

		# Write to file
		hdu.writeto(waterfall_fits)

	# Write Spectra to file (csv)
	if spectra_csv != '':
		if cal_file != '':
			np.savetxt(spectra_csv, np.concatenate((frequency.reshape(channels, 1),
                       avg_spectrum.reshape(channels, 1), avg_spectrum_cal.reshape(channels, 1),
                       spectrum.reshape(channels, 1)), axis=1), delimiter=',', fmt='%1.3f')
		else:
			np.savetxt(spectra_csv, np.concatenate((frequency.reshape(channels, 1),
                       avg_spectrum.reshape(channels, 1)), axis=1), delimiter=',', fmt='%1.3f')

	# Write Time Series to file (csv)
	if power_csv != '':
		np.savetxt(power_csv, np.concatenate((t.reshape(subs, 1), power.reshape(subs, 1)),
                   axis=1), delimiter=',', fmt='%1.3f')

	# Initialize plot
	if cal_file != '':
		fig = plt.figure(figsize=(27, 15))
		gs = GridSpec(2, 3)
	else:
		fig = plt.figure(figsize=(21, 15))
		gs = GridSpec(2, 2)

	# Plot Average Spectrum
	ax1 = fig.add_subplot(gs[0, 0])
	ax1.plot(frequency, avg_spectrum)
	if xlim == [0,0]:
		ax1.set_xlim(np.min(frequency), np.max(frequency))
	else:
		ax1.set_xlim(xlim[0], xlim[1])
	ax1.ticklabel_format(useOffset=False)
	ax1.set_xlabel('Frequency (MHz)')
	if dB:
		ax1.set_ylabel('Relative Power (dB)')
	else:
		ax1.set_ylabel('Relative Power')
	if f_rest != 0 and sys.version_info[0] < 3:
		ax1.set_title('Average Spectrum\n')
	else:
		ax1.set_title('Average Spectrum')
	ax1.grid()

	if xlim == [0,0] and f_rest != 0:
		# Add secondary axis for Radial Velocity
		ax1_secondary = ax1.twiny()
		ax1_secondary.set_xlabel('Radial Velocity (km/s)', labelpad=5)
		ax1_secondary.axvline(x=0, color='brown', linestyle='--', linewidth=2, zorder=0)
		ax1_secondary.annotate('Spectral Line\nRest Frequency', xy=(460-text_offset, 5),
                               xycoords='axes points', size=14, ha='left', va='bottom', color='brown')
		ax1_secondary.set_xlim(left_velocity_edge, right_velocity_edge)
		ax1_secondary.tick_params(axis='x', direction='in', pad=-22)

	#Plot Calibrated Spectrum
	if cal_file != '':
		ax2 = fig.add_subplot(gs[0, 1])
		ax2.plot(frequency, spectrum, label='Raw Spectrum')
		if n != 0:
			ax2.plot(frequency, spectrum_clean, color='orangered', label='Median (n = '+str(n)+')')
			ax2.set_ylim()
		if xlim == [0,0]:
			ax2.set_xlim(np.min(frequency), np.max(frequency))
		else:
			ax2.set_xlim(xlim[0], xlim[1])
		ax2.ticklabel_format(useOffset=False)
		ax2.set_xlabel('Frequency (MHz)')
		ax2.set_ylabel('Signal-to-Noise Ratio (S/N)')
		if f_rest != 0 and sys.version_info[0] < 3:
			ax2.set_title('Calibrated Spectrum\n')
		else:
			ax2.set_title('Calibrated Spectrum')
		if n != 0:
			if f_rest != 0:
				ax2.legend(bbox_to_anchor=(0.002, 0.96), loc='upper left')
			else:
				ax2.legend(loc='upper left')

		if xlim == [0,0] and f_rest != 0:
			# Add secondary axis for Radial Velocity
			ax2_secondary = ax2.twiny()
			ax2_secondary.set_xlabel('Radial Velocity (km/s)', labelpad=5)
			ax2_secondary.axvline(x=0, color='brown', linestyle='--', linewidth=2, zorder=0)
			ax2_secondary.annotate('Spectral Line\nRest Frequency', xy=(400, 5),
                                   xycoords='axes points', size=14, ha='left', va='bottom', color='brown')
			ax2_secondary.set_xlim(left_velocity_edge, right_velocity_edge)
			ax2_secondary.tick_params(axis='x', direction='in', pad=-22)
		ax2.grid()

	# Plot Dynamic Spectrum
	if cal_file != '':
		ax3 = fig.add_subplot(gs[0, 2])
	else:
		ax3 = fig.add_subplot(gs[0, 1])

	ax3.imshow(decibel(waterfall), origin='lower', interpolation='None', aspect='auto',
		   extent=[np.min(frequency), np.max(frequency), np.min(t), np.max(t)])
	if xlim == [0,0] and ylim != [0,0]:
		ax3.set_ylim(ylim[0], ylim[1])
	elif xlim != [0,0] and ylim == [0,0]:
		ax3.set_xlim(xlim[0], xlim[1])
	elif xlim != [0,0] and ylim != [0,0]:
		ax3.set_xlim(xlim[0], xlim[1])
		ax3.set_ylim(ylim[0], ylim[1])

	ax3.ticklabel_format(useOffset=False)
	ax3.set_xlabel('Frequency (MHz)')
	ax3.set_ylabel('Relative Time (s)')
	ax3.set_title('Dynamic Spectrum (Waterfall)')

	# Adjust Subplot Width Ratio
	if cal_file != '':
		gs = GridSpec(2, 3, width_ratios=[16.5, 1, 1])
	else:
		gs = GridSpec(2, 2, width_ratios=[7.6, 1])

	# Plot Time Series (Power vs Time)
	ax4 = fig.add_subplot(gs[1, 0])
	ax4.plot(t, power, label='Raw Time Series')
	if m != 0:
		ax4.plot(t, power_clean, color='orangered', label='Median (n = '+str(m)+')')
		ax4.set_ylim()
	if ylim == [0,0]:
		ax4.set_xlim(0, np.max(t))
	else:
		ax4.set_xlim(ylim[0], ylim[1])
	ax4.set_xlabel('Relative Time (s)')
	if dB:
		ax4.set_ylabel('Relative Power (dB)')
	else:
		ax4.set_ylabel('Relative Power')
	ax4.set_title('Average Power vs Time')
	if m != 0:
		ax4.legend(bbox_to_anchor=(1, 1), loc='upper right')
	ax4.grid()

	# Plot Total Power Distribution
	if cal_file != '':
		gs = GridSpec(2, 3, width_ratios=[7.83, 1.5, -0.325])
	else:
		gs = GridSpec(2, 2, width_ratios=[8.8, 1.5])

	ax5 = fig.add_subplot(gs[1, 1])

	ax5.hist(power, np.max([int(np.size(power)/50),10]), density=1, alpha=0.5, color='royalblue', orientation='horizontal', zorder=10)
	ax5.plot(best_fit(power)[1], best_fit(power)[0], '--', color='blue', label='Best fit (Raw)', zorder=20)
	if m != 0:
		ax5.hist(power_clean, np.max([int(np.size(power_clean)/50),10]), density=1, alpha=0.5, color='orangered', orientation='horizontal', zorder=10)
		ax5.plot(best_fit(power_clean)[1], best_fit(power_clean)[0], '--', color='red', label='Best fit (Median)', zorder=20)
	ax5.set_xlim()
	ax5.set_ylim()
	ax5.get_shared_x_axes().join(ax5, ax4)
	ax5.set_yticklabels([])
	ax5.set_xlabel('Probability Density')
	ax5.set_title('Total Power Distribution')
	ax5.legend(bbox_to_anchor=(1, 1), loc='upper right')
	ax5.grid()

	# Save plots to file
	plt.tight_layout()
	plt.savefig(plot_file)
	plt.clf()

def plot_rfi(rfi_parameters, data='rfi_data', dB=True, plot_file='plot.png'):
	import matplotlib
	matplotlib.use('Agg') # Try commenting this line if you run into display/rendering errors
	import matplotlib.pyplot as plt
	from matplotlib.gridspec import GridSpec

	plt.rcParams['legend.fontsize'] = 18
	plt.rcParams['axes.labelsize'] = 40
	plt.rcParams['axes.titlesize'] = 50
	plt.rcParams['xtick.labelsize'] = 34
	plt.rcParams['ytick.labelsize'] = 34

	f_lo = rfi_parameters['f_lo']
	bandwidth = rfi_parameters['bandwidth']
	channels = rfi_parameters['channels']
	t_sample = rfi_parameters['t_sample']
	duration = rfi_parameters['duration']

	def decibel(x):
		if dB: return 10.0*np.log10(x)
		return x

	# Transform sampling time to number of bins
	bins = int(t_sample*bandwidth/channels)

	offset = 1
	total = []

	# Count number of .dat files
	n = len([f for f in os.listdir(data) if f.endswith('.dat') and os.path.isfile(os.path.join(data, f))])

	for i in range(int(n)):
		# Load data
		waterfall = offset*np.fromfile(data+'/'+str(i)+'.dat', dtype='float32').reshape(-1, channels)/bins

		# Delete first 3 rows (potentially containing outlier samples)
		waterfall = waterfall[3:, :]

		total.append(waterfall)

	# Merge dynamic spectra
	combined = np.concatenate(total, axis=1)

	# Compute average spectra
	avg_spectrum = np.mean(combined, axis=0)

	# Compute frequency axis
	allfreq = []

	for i in range(int(n)):
		f_total = np.linspace((f_lo+bandwidth*i)-0.5*bandwidth, (f_lo+bandwidth*i)+0.5*bandwidth, channels, endpoint=False)*1e-6
		allfreq.append(f_total)

	f_total = np.concatenate(allfreq)

	# Initialize plot
	fig = plt.figure(figsize=(5*n,20.25))
	gs = GridSpec(1,1)

	# Plot merged spectra
	ax = fig.add_subplot(gs[0,0])

	ax.plot(f_total, decibel(avg_spectrum), '#3182bd')
	ax.set_ylim()
	ax.fill_between(f_total, decibel(avg_spectrum), y2=-1000, color='#deebf7')
	ax.set_xlim(np.min(f_total), np.max(f_total))
	ax.ticklabel_format(useOffset=False)
	ax.set_xlabel('Frequency (MHz)')

	if dB:
		ax.set_ylabel('Relative Power (dB)', x=-10)
	else:
		ax.set_ylabel('Relative Power', x=-10)

	ax.set_title('Average RFI Spectrum', y=1.0075)

	ax.annotate('Monitored frequency range: '+str(round(f_lo/1000000,1))+'-'+str(round(np.max(f_total),1))+' MHz ($\\Delta\\nu$ = '+str(round(((np.max(f_total)*1e6-f_lo)/1000000),1))+
' MHz)\nBandwidth per spectrum: '+str(bandwidth/1000000)+' MHz\nIntegration time per spectrum: '+str(duration)+' sec\nFFT size: '+str(channels), xy=(17, 1290),
xycoords='axes points', size=32, ha='left', va='top', color='brown')

	ax.grid()

	plt.tight_layout()
	plt.savefig(plot_file)
	plt.clf()

def monitor_rfi(f_lo, f_hi, obs_parameters, data='rfi_data'):
	dev_args = obs_parameters['dev_args']
	rf_gain = obs_parameters['rf_gain']
	if_gain = obs_parameters['if_gain']
	bb_gain = obs_parameters['bb_gain']
	bandwidth = obs_parameters['bandwidth']
	channels = obs_parameters['channels']
	duration = obs_parameters['duration']

	t_sample = 0.1

	# Create RFI data directory
	if os.path.exists(data):
		shutil.rmtree(data)

	os.makedirs(data)

	# Iterate over the input frequency range
	i = 0
	for frequency in range(int(f_lo), int(f_hi), int(bandwidth)):
		rfi_parameters = {
			'dev_args': dev_args,
			'rf_gain': rf_gain,
			'if_gain': if_gain,
			'bb_gain': bb_gain,
			'frequency': frequency,
			'bandwidth': bandwidth,
			'channels': channels,
			't_sample': t_sample,
			'duration': duration,
			'f_lo': f_lo
		}

		# Run RFI monitor
		observe(obs_parameters=rfi_parameters, spectrometer='ftf', obs_file=data+'/'+str(i)+'.dat')
		i += 1

if __name__ == '__main__':
	# Load argument values
	parser = argparse.ArgumentParser()

	parser.add_argument('-da', '--dev_args', dest='dev_args',
                        help='SDR Device Arguments (osmocom Source)', type=str, default='')
	parser.add_argument('-rf', '--rf_gain', dest='rf_gain',
                        help='SDR RF Gain (dB)', type=float, default=10)
	parser.add_argument('-if', '--if_gain', dest='if_gain',
                        help='SDR IF Gain (dB)', type=float, default=20)
	parser.add_argument('-bb', '--bb_gain', dest='bb_gain',
                        help='SDR BB Gain (dB)', type=float, default=20)
	parser.add_argument('-f', '--frequency', dest='frequency',
                        help='Center Frequency (Hz)', type=float, required=True)
	parser.add_argument('-b', '--bandwidth', dest='bandwidth',
                        help='Bandwidth (Hz)', type=float, required=True)
	parser.add_argument('-c', '--channels', dest='channels',
                        help='Number of Channels (FFT Size)', type=int, required=True)
	parser.add_argument('-t', '--t_sample', dest='t_sample',
                        help='FFT Sample Time (s)', type=float, required=True)
	parser.add_argument('-d', '--duration', dest='duration',
                        help='Observing Duration (s)', type=float, default=60)
	parser.add_argument('-s', '--start_in', dest='start_in',
                        help='Schedule Observation (s)', type=float, default=0)
	parser.add_argument('-o', '--obs_file', dest='obs_file',
                        help='Observation Filename', type=str, default='observation.dat')
	parser.add_argument('-C', '--cal_file', dest='cal_file',
                        help='Calibration Filename', type=str, default='')
	parser.add_argument('-db', '--db', dest='dB',
                        help='Use dB-scaled Power values', default=False, action='store_true')
	parser.add_argument('-n', '--median_frequency', dest='n',
                        help='Median Factor (Frequency Domain)', type=int, default=0)
	parser.add_argument('-m', '--median_time', dest='m',
                        help='Median Factor (Time Domain)', type=int, default=0)
	parser.add_argument('-r', '--rest_frequency', dest='f_rest',
                        help='Spectral Line Rest Frequency (Hz)', type=float, default=0)
	parser.add_argument('-W', '--waterfall_fits', dest='waterfall_fits',
                        help='Filename for FITS Waterfall File', type=str, default='')
	parser.add_argument('-S', '--spectra_csv', dest='spectra_csv',
                        help='Filename for Spectra csv File', type=str, default='')
	parser.add_argument('-P', '--power_csv', dest='power_csv',
                        help='Filename for Spectra csv File', type=str, default='')
	parser.add_argument('-p', '--plot_file', dest='plot_file',
                        help='Plot Filename', type=str, default='plot.png')

	args = parser.parse_args()

	# Define data-acquisition parameters
	observation = {
	'dev_args': args.dev_args,
    'rf_gain': args.rf_gain,
    'if_gain': args.if_gain,
    'bb_gain': args.bb_gain,
    'frequency': args.frequency,
    'bandwidth': args.bandwidth,
    'channels': args.channels,
    't_sample': args.t_sample,
    'duration': args.duration
	}

	# Acquire data from SDR
	#observe(obs_parameters=observation, obs_file=args.obs_file, start_in=args.start_in)

	# Plot data
	plot(obs_parameters=observation, n=args.n, m=args.m, f_rest=args.f_rest,
	     dB=args.dB, obs_file=args.obs_file, cal_file=args.cal_file, waterfall_fits=args.waterfall_fits,
		 spectra_csv=args.spectra_csv, power_csv=args.power_csv, plot_file=args.plot_file)