import math as m
import os
import scipy
import numpy as np
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess as sp

import scipy.io.wavfile as wavfile
from scipy import signal
from matplotlib import pyplot as plt

import cv2
from cv2 import VideoWriter, VideoWriter_fourcc
from PIL import Image, ImageDraw, ImageFont
import colorsys


FONT_PATH = Path(__file__).resolve().parent / "fonts" / "NexaText-Trial-Light.ttf"


def _output_song_dir(songTitle):
    return Path("songs") / songTitle / "outputs"


def _newest_file(paths):
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _find_output_audio(songTitle, songOutputDir):
    finishedDir = songOutputDir / "_finished"
    exact_mp3_paths = [
        finishedDir / f"{songTitle}.mp3",
        songOutputDir / f"{songTitle}.mp3",
    ]
    audioFile = _newest_file(exact_mp3_paths)
    if audioFile:
        return audioFile

    mp3_paths = []
    for searchDir in (finishedDir, songOutputDir):
        if searchDir.exists():
            mp3_paths.extend(searchDir.glob("*.mp3"))
    audioFile = _newest_file(mp3_paths)
    if audioFile:
        return audioFile

    exact_wav_paths = [
        finishedDir / f"{songTitle}.wav",
        songOutputDir / f"{songTitle}.wav",
    ]
    audioFile = _newest_file(exact_wav_paths)
    if audioFile:
        return audioFile

    wav_paths = []
    for searchDir in (finishedDir, songOutputDir):
        if searchDir.exists():
            wav_paths.extend(searchDir.glob("*.wav"))
    return _newest_file(wav_paths)


def _media_duration_seconds(mediaFile):
    try:
        result = sp.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(mediaFile),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        if mediaFile.suffix.lower() == ".wav":
            try:
                samplingRate, data = wavfile.read(str(mediaFile))
                return len(data) / samplingRate
            except Exception:
                pass
    return None


def _bar_dimensions(videoDims, trackPosition, barCount, barGapFrac):
    sizeFrac = max(0.01, min(1.0, float(trackPosition[0])))
    xFrac = max(0.0, min(1.0 - sizeFrac, float(trackPosition[1])))
    yFrac = max(0.0, min(1.0 - sizeFrac, float(trackPosition[2])))

    pixelWidth = videoDims[0] * sizeFrac
    pixelHeight = videoDims[1] * sizeFrac
    barSpacing = pixelWidth / (barCount + 1)
    barWidth = barSpacing * barGapFrac
    maxHeight = pixelHeight / 2
    xLeftEdge = videoDims[0] * xFrac
    yCenter = videoDims[1] * (yFrac + sizeFrac/2)
    return (barSpacing, barWidth, maxHeight, xLeftEdge, yCenter)


def _prepare_track_spectrogram(track_name, wav_path, frames_per_second, freq_range, bar_count):
    """Build one track's normalized FFT frame data; safe to run alongside other tracks."""
    sampling_rate, wave_data = wavfile.read(str(wav_path))
    proc_spec = signal.spectrogram(
        wave_data,
        sampling_rate,
        window=("hamming"),
        nperseg=int(sampling_rate / frames_per_second),
    )
    time_range = len(wave_data) / sampling_rate
    frame_count = max(1, m.ceil(time_range * frames_per_second))
    spec_data = proc_spec[2]

    fps_adjusted = np.zeros((len(spec_data), frame_count), dtype=np.float32)
    target_frames = np.arange(frame_count)
    source_frames = np.linspace(0, frame_count - 1, len(spec_data[0]))
    for index in range(len(spec_data)):
        fps_adjusted[index] = np.interp(target_frames, source_frames, spec_data[index])
    fps_adjusted = np.transpose(fps_adjusted)

    freq_domain = proc_spec[0]
    freq_min_index = (np.abs(freq_domain - freq_range[0])).argmin()
    freq_max_index = (np.abs(freq_domain - freq_range[1])).argmin()
    bar_adjusted = np.zeros((len(fps_adjusted), bar_count), dtype=np.float32)
    frequency_targets = np.arange(bar_count)
    frequency_source = np.arange(
        freq_min_index,
        freq_max_index,
        (freq_max_index - freq_min_index) / len(fps_adjusted[0]),
    )
    for index in range(len(fps_adjusted)):
        bar_adjusted[index] = np.interp(frequency_targets, frequency_source, fps_adjusted[index])

    np.sqrt(np.abs(bar_adjusted), out=bar_adjusted)
    spec_max = np.max(bar_adjusted, axis=1)
    spec_max = np.sort(spec_max[spec_max > 0.0])
    normalization = spec_max[m.floor(len(spec_max) * 0.9)] if len(spec_max) else 1.0
    bar_adjusted /= normalization
    np.minimum(bar_adjusted, 1.0, out=bar_adjusted)
    return {
        "track": track_name,
        "data": bar_adjusted,
        "frame_count": frame_count,
        "time_range": time_range,
        "freq_min_index": freq_min_index,
        "freq_max_index": freq_max_index,
        "freq_bin_count": len(fps_adjusted[0]),
        "normalization": normalization,
    }




def generateAnimation(trackNames, songTitle, settings_yaml, videoDims=(2560, 1440), freqRange=(100, 5000), divisionFactor = 500, framesPerSecond=30, barCount=100, back_color=[0, 0, 0], barGapFrac = 0.5):
    songOutputDir = _output_song_dir(songTitle)
    tracksDir = songOutputDir / "_tracks"
    finishedDir = songOutputDir / "_finished"
    finishedDir.mkdir(parents=True, exist_ok=True)

    back_color = colorsys.hsv_to_rgb(back_color[0]/360, back_color[1]/100, back_color[2]/100)
    back_color = tuple([int(255*foo) for foo in back_color] )
    print(f"   back_color:{back_color}")
    
    labelFont = ImageFont.truetype(str(FONT_PATH), 80)
    
    spectDict = {}
    animationFrameCount = 0
    worker_count = min(len(trackNames), max(1, min(4, os.cpu_count() or 1)))
    print(f"Preparing {len(trackNames)} spectrograms across {worker_count} CPU workers")
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="choir-spectrogram") as executor:
        prepared = {
            fooTrack: executor.submit(
                _prepare_track_spectrogram,
                fooTrack,
                tracksDir / f"{fooTrack}.wav",
                framesPerSecond,
                freqRange,
                barCount,
            )
            for fooTrack in trackNames
        }
        for fooTrack in trackNames:
            track_data = prepared[fooTrack].result()
            print(f"\n{fooTrack}:")
            fooTrackDict = settings_yaml['Tracks'][fooTrack]
            bar_color = fooTrackDict['VID_HSB']
            bar_color = colorsys.hsv_to_rgb(bar_color[0]/360, bar_color[1]/100, bar_color[2]/100)
            bar_color = tuple([int(255*foo) for foo in bar_color])
            print(f"   bar_color:{bar_color}")
            print(f"   timeRange:{track_data['time_range']}")
            print(f"   frameCount:{track_data['frame_count']}")
            print(f"   freq range: {track_data['freq_min_index']}->{track_data['freq_max_index']} out of {track_data['freq_bin_count']}")
            print(f"   divisionFactor:{track_data['normalization']}")

            animationFrameCount = max(animationFrameCount, track_data['frame_count'])
            fooPos = fooTrackDict['VID_Position']
            spectDict[fooTrack] = {
                'data': track_data['data'],
                'color': bar_color,
                'label': fooTrackDict['VID_Label'],
                'position': fooPos,
                'currFFT': deepcopy(track_data['data'][0]),
                'frameCount': track_data['frame_count'],
                'barDims': _bar_dimensions(videoDims, fooPos, barCount, barGapFrac),
            }

    audioFileName = _find_output_audio(songTitle, songOutputDir)
    if audioFileName:
        audioDuration = _media_duration_seconds(audioFileName)
        if audioDuration:
            audioFrameCount = max(1, m.ceil(audioDuration*framesPerSecond))
            animationFrameCount = max(animationFrameCount, audioFrameCount)
            print(f"\nAudio for final output: {audioFileName}")
            print(f"   audioDuration:{audioDuration}")
            print(f"   audioFrameCount:{audioFrameCount}")
    else:
        print(f"\nNo final audio found under {songOutputDir}; animation will be exported without muxing.")

    # Setup video output
    vidWidth = videoDims[0]
    vidHeight = videoDims[1]

    # Setup video output
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')    
    outputFileName = finishedDir / "animation.mp4"
    video = cv2.VideoWriter(str(outputFileName), fourcc, framesPerSecond, videoDims)

    # Setup Pillow Image
    # Each frame is written to this image and then saved to video
    imtemp = Image.new("RGB", videoDims, (255, 255, 255))
    draw = ImageDraw.Draw(imtemp)


    # # Calculate variables for bar plot
    # maxHeight = vidHeight/2
    # barWidth = (vidWidth -edgeMargin*2)/barCount
    # barHalfGap = barWidth*barGapFrac/2

    # currFFT = None
    # Iterate over data
    # for yy in range(int(frameCount/10)):
    for yy in range(animationFrameCount):
        print(f"Frame: {yy}\033[F") # Print current iteration to same line
        draw.rectangle((0, 0, vidWidth, vidHeight), fill=back_color) # Reset image

        for fooTrack in spectDict:
            fooDict = spectDict[fooTrack]

            if len(fooDict['data']) <= yy: continue
            currFFT = fooDict['currFFT']
            barSpace, barWidth, maxHeight, xLeftEdge, yCenter = fooDict['barDims']
            fooFFt = fooDict['data'][yy]

            # Process each bar
            for xx in range(barCount):
                # Smooths data in time domain, preventing sharp drop offs
                currFFT[xx] /= 1.4
                if currFFT[xx] < fooFFt[xx]: currFFT[xx] = fooFFt[xx]
                
                # Load value for bar and clamp
                ptValue = currFFT[xx]
                if ptValue > 1.0: ptValue = 1.0
                if ptValue < 0.001: ptValue = 0.001 


                # Actually draw bar
                draw.ellipse(
                    (((xx)*barSpace +xLeftEdge, yCenter-currFFT[xx]*maxHeight, (xx)*barSpace+barWidth +xLeftEdge, yCenter+currFFT[xx]*maxHeight)), 
                    fill=fooDict['color'])
                
                # if(currFFT[xx] > 0.5): print(((xx)*barSpace +xLeftEdge, currFFT[xx]*maxHeight+yCenter, (xx)*barSpace+barWidth +xLeftEdge, -currFFT[xx]*maxHeight+yCenter))
                

                # draw.ellipse(
                #     (xx*barWidth +barHalfGap+edgeMargin, maxHeight-maxHeight*ptValue, 
                #         (xx+1)*barWidth -barHalfGap+edgeMargin, maxHeight+maxHeight*ptValue), 
                #     fill=bar_color)
            
            fooDict['currFFT'] = currFFT

            # if max(currFFT) > 0.9: 
            #     imtemp.show()
                # input('PAUSED')
            
            # print(max(currFFT)*maxHeight)
            # currentTime = yy/framesPerSecond
            # if currentTime > trackSettingDict['VID_LabelTime'] and currentTime < trackSettingDict['VID_LabelTime'] +trackSettingDict["VID_LabelDur"] +trackSettingDict['VID_LabelFade']:
            #     textOpacity = 1.0
            #     if currentTime > trackSettingDict['VID_LabelTime'] +trackSettingDict["VID_LabelDur"]:
            #         # print("!!!!!!!!   ", end='')
            #         textOpacity =  ( 1 - (currentTime -trackSettingDict['VID_LabelTime'] -trackSettingDict["VID_LabelDur"])/trackSettingDict['VID_LabelFade'] )

            #         # print((currentTime -trackSettingDict['VID_LabelTime'] -trackSettingDict["VID_LabelDur"])/trackSettingDict['VID_LabelFade'])

            #     draw.text(labelPosition, labelText, (m.floor(bar_color[0] * textOpacity), m.floor(bar_color[1] * textOpacity), m.floor(bar_color[2] * textOpacity)), labelFont)

        video.write(cv2.cvtColor(np.array(imtemp), cv2.COLOR_RGB2BGR)) # Save frame

    video.release() # Output final video


    # Use ffmpeg to mix output audio and video to single, synced file
    if not audioFileName:
        return

    print(f"Adding audio for final output")
    finalFileName = finishedDir / f"{songTitle}.mp4"
    audioArgs = ["-c:a", "copy", "-strict", "-1"] if audioFileName.suffix.lower() == ".mp3" else ["-c:a", "aac"]
    sp.run(
        [
            "ffmpeg", "-y",
            "-i", str(outputFileName),
            "-i", str(audioFileName),
            "-c:v", "copy",
            *audioArgs,
            "-map", "0:v:0",
            "-map", "1:a:0",
            str(finalFileName),
        ],
        check=True,
    )
    if finalFileName.is_file() and finalFileName.stat().st_size > 0:
        outputFileName.unlink(missing_ok=True)
        print(f"Removed intermediate animation: {outputFileName}")
