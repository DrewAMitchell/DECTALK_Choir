import math as m
import scipy
import numpy as np
from copy import deepcopy

import scipy.io.wavfile as wavfile
from scipy import signal
from matplotlib import pyplot as plt

import cv2
from cv2 import VideoWriter, VideoWriter_fourcc
from PIL import Image, ImageDraw

import sys
import os
import time
from pathlib import Path

import pyFuncs.spectrogramAnimation as specAnimate


BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

DEFAULT_VIDEO_DIMENSIONS = (2560, 1440) # Width and height of video if host size is unavailable
framesPerSecond = 30 # Output FPS


def _even_dimension(value):
    value = max(2, int(round(float(value))))
    if value % 2:
        value -= 1
    return value


def _parse_video_dimensions(value):
    if value is None:
        return None
    if isinstance(value, str):
        for delimiter in ('x', 'X', ',', ' '):
            value = value.replace(delimiter, ' ')
        parts = [part for part in value.split() if part]
    else:
        parts = list(value)
    if len(parts) != 2:
        return None
    try:
        return (_even_dimension(parts[0]), _even_dimension(parts[1]))
    except (TypeError, ValueError):
        return None


def _host_video_dimensions():
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        user32 = ctypes.windll.user32
        dims = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        if dims[0] > 0 and dims[1] > 0:
            return (_even_dimension(dims[0]), _even_dimension(dims[1]))
    except Exception:
        pass

    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        dims = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        if dims[0] > 0 and dims[1] > 0:
            return (_even_dimension(dims[0]), _even_dimension(dims[1]))
    except Exception:
        pass

    return None


def _video_dimensions(settings_yaml):
    env_dims = _parse_video_dimensions(os.environ.get('DECTALK_VIDEO_SIZE'))
    if env_dims:
        print(f"Using DECTALK_VIDEO_SIZE: {env_dims}")
        return env_dims

    configured_dims = _parse_video_dimensions(settings_yaml.get('videoDimensions'))
    if configured_dims:
        print(f"Using settings videoDimensions: {configured_dims}")
        return configured_dims

    host_dims = _host_video_dimensions()
    if host_dims:
        print(f"Using host video dimensions: {host_dims}")
        return host_dims

    print(f"Using default video dimensions: {DEFAULT_VIDEO_DIMENSIONS}")
    return DEFAULT_VIDEO_DIMENSIONS


# Make sure song is specified in command
if len(sys.argv) < 2:
    print('No song specified')
    exit()

songTitle = sys.argv[-1]

# Make sure specified song folder exists
if not songTitle in os.listdir('songs'):
    print(f'Songs folders found are:')
    for foo in os.listdir('songs'):
        print(f"   {foo}")
    print(f'Song {songTitle} not found')
    exit()

# Load settings.yaml from within folder
import yaml
try:
    file = open(f"songs/{songTitle}/settings.yaml", 'r')
    settings_yaml = yaml.safe_load(file)
except:
    print(f"songs/{songTitle}/settings.yaml NOT FOUND")
    exit()

videoDimensions = _video_dimensions(settings_yaml)

# Folder for output files
songOutputDir = f"songs/{songTitle}/outputs"
os.makedirs(f"{songOutputDir}/_animation", exist_ok = True)

wavFileList = os.listdir(f"{songOutputDir}/_tracks/")
wavFileList = [foo.split('.')[0] for foo in wavFileList if '.wav' in foo]

trackList = [foo for foo in settings_yaml['Tracks']]

outputParts = []
ii=0
while ii<len(trackList):
    foo = trackList[ii]
    if foo in wavFileList:
        outputParts.append(foo)
        wavFileList.remove(foo)
        trackList.remove(foo)
    else: 
        ii+= 1

print(f"Only .wav:{wavFileList}")
print(f"Only settings:{trackList}")
print(f"outputParts:{outputParts}")

requestedParts = [part.strip() for part in os.environ.get("DECTALK_CHOIR_SPECTROGRAM_ROLES", "").split(",") if part.strip()]
if requestedParts:
    unavailableParts = [part for part in requestedParts if part not in outputParts]
    outputParts = [part for part in outputParts if part in requestedParts]
    if unavailableParts:
        print(f"Enabled tracks without rendered stems:{unavailableParts}")
    print(f"Spectrogram tracks:{outputParts}")
if not outputParts:
    print("No enabled rendered stems are available for spectrogram generation")
    exit(1)

# Add default settings to dictionary if none found
for fooTrack in outputParts:
    trackDict = settings_yaml['Tracks'][fooTrack]
    if 'VID_HSB' not in trackDict: trackDict['VID_HSB'] = [0, 100, 100]
    if 'VID_Position' not in trackDict: trackDict['VID_Position'] = [0.5, 0.25, 0.25]
    if 'VID_Label' not in trackDict: trackDict['VID_Label'] = fooTrack
    if 'VID_LabelTime' not in trackDict: trackDict['VID_LabelTime'] = 0.0
    if 'VID_LabelDur' not in trackDict: trackDict['VID_LabelDur'] = 4.0
    if 'VID_LabelFade' not in trackDict: trackDict['VID_LabelFade'] = 1.0

# 
# for fooTrack in outputParts:
#     print(f"\nAnimating {fooTrack}")
#     wavFileName = f"{songOutputDir}/_tracks/{fooTrack}.wav"
#     outputFileName = f"{songOutputDir}/_animation/{fooTrack}.mp4"

backColor = [100, 0, 0]
specAnimate.generateAnimation(outputParts, songTitle, settings_yaml, videoDims=videoDimensions, framesPerSecond=30, back_color=backColor)

print("DONE!")
exit()

# # Setup video output
# outputFileName = f"{songOutputDir}/_finished/{songTitle}.mp4"
# fourcc = cv2.VideoWriter_fourcc(*'mp4v')
# video = cv2.VideoWriter(outputFileName, fourcc, framesPerSecond, videoDimensions)


# clipSet = []
# for fooTrack in outputParts:
#     vidFileName = f"{songOutputDir}/_animation/{fooTrack}.mp4"
#     vid = cv2.VideoCapture(vidFileName)

#     trackSettings = settings_yaml['Tracks'][fooTrack]['VID_Position']
#     xPixelSize = int( videoDimensions[0]*trackSettings[0] )
#     yPixelSize = int( videoDimensions[1]*trackSettings[0] )
    
#     clipSet.append({
#         'vid': vid,
#         'size':(xPixelSize, yPixelSize),
#         'xStart': int(videoDimensions[0]*trackSettings[1]),
#         'yStart': int(videoDimensions[1]*trackSettings[2]),
#     })
#     # print(trackSettings)
#     # print(clipSet[-1])

# print("Compositing videos")
# ii = 0
# while(len(clipSet) > 0):
#     print(f"{ii}\033[F")
#     # print(f"{len(clipSet)}")
#     ii += 1
#     img = None
    
#     jj = 0
#     while jj < len(clipSet):
#         fooClip = clipSet[jj]
#         ret, frame = fooClip['vid'].read()
        
#         if not ret: 
#             clipSet.pop(jj)
#             continue

#         if type(img) != type(frame): img = np.zeros_like(frame)

#         # print(frame.shape)
        
#         imgResized = cv2.resize(frame, (fooClip['size']), interpolation = cv2.INTER_AREA)

#         # print('\n\n\n')
#         # print(f"imgResized.shape:{imgResized.shape}")
#         # print(f"fooClip['yStart']:{fooClip['yStart']}")
#         # print(f"fooClip['yStart']+fooClip['size'][1]:{fooClip['yStart']+fooClip['size'][1]}")
#         # print(f"fooClip['xStart']:{fooClip['xStart']}")
#         # print(f"fooClip['xStart']+fooClip['size'][0]:{fooClip['xStart']+fooClip['size'][0]}")

#         img[fooClip['yStart']:fooClip['yStart']+fooClip['size'][1], fooClip['xStart']:fooClip['xStart']+fooClip['size'][0]] = imgResized
#         jj += 1
        
#     video.write(img)
#     if cv2.waitKey(1) & 0xFF == ord('q'):
#         cv2.destroyAllWindows()
#         break

# video.release()



# # Use ffmpeg to mix output audio and video to single, synced file
# print(f"Adding audio for final output")
# audioFileName = f"{songOutputDir}/_finished/{songTitle}.wav"
# finalFileName = f"{songOutputDir}/{songTitle}.mp4"
# import subprocess as sp
# sp.run(f"ffmpeg -y -i {outputFileName} -i {audioFileName} -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 {finalFileName}", shell=True)
