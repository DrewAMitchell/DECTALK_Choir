import math as m
import scipy
import numpy as np
from copy import deepcopy
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
    repo_relative = Path("outputs") / songTitle
    output_relative = Path(songTitle)
    cwd = Path.cwd()

    if repo_relative.exists():
        return repo_relative
    if output_relative.exists():
        return output_relative
    if cwd.name.lower() == songTitle.lower():
        return Path(".")
    return repo_relative


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
    for fooTrack in trackNames:
        print(f"\n{fooTrack}:")
        fooTrackDict = settings_yaml['Tracks'][fooTrack]
        # Convert input colors to HSV
        bar_color = fooTrackDict['VID_HSB']
        bar_color = colorsys.hsv_to_rgb(bar_color[0]/360, bar_color[1]/100, bar_color[2]/100)
        bar_color = tuple([int(255*foo) for foo in bar_color] )
        print(f"   bar_color:{bar_color}")



        
        # Load waveFile to process
        readWav = wavfile.read(str(tracksDir / f"{fooTrack}.wav"))
        samplingRate = readWav[0]

        # Run spectrogram on waveFile
        procSpec = signal.spectrogram(readWav[1], samplingRate, window=('hamming'), nperseg=int(readWav[0]/framesPerSecond)) # nperseg gets close to target framesPerSecond


        # Calculate length of wav in seconds
        timeRange = len(readWav[1]) / samplingRate
        print(f"   timeRange:{timeRange}")

        # Get just spectrogram data
        specData = procSpec[2]
        
        frameCount = max(1, m.ceil(timeRange*framesPerSecond))
        animationFrameCount = max(animationFrameCount, frameCount)
        print(f"   frameCount:{frameCount}")

        # Interpolate spectrogram to match time domain to FPS
        fpsAdjustedSpec = np.zeros((len(specData), frameCount), dtype=np.float32)
        targetFrames = np.arange(0, frameCount, 1)
        sourceFrames = np.linspace(0, frameCount - 1, len(specData[0]))
        for ii in range(len(specData)):
            fpsAdjustedSpec[ii] = np.interp(targetFrames, sourceFrames, specData[ii])
        
        # Flip array so axis 0 is time and axis 1 is frequency
        fpsAdjustedSpec = np.transpose(fpsAdjustedSpec)

        # Get min and max index of frequency domain which matches freqRange
        freqDomain = procSpec[0]
        freqMinInd = (np.abs(freqDomain - freqRange[0])).argmin()
        freqMaxInd = (np.abs(freqDomain - freqRange[1])).argmin()

        print(f"   freq range: {freqMinInd}->{freqMaxInd} out of {len(fpsAdjustedSpec[ii])}")

        #  Interpolate spectrogram to match frequency domain barCount
        barAdjustedSpec = np.zeros((len(fpsAdjustedSpec), barCount), dtype=np.float32)
        for ii in range(len(fpsAdjustedSpec)):
            barAdjustedSpec[ii] = np.interp( 
                np.arange(barCount),
                np.arange(freqMinInd, freqMaxInd, (freqMaxInd-freqMinInd)/len(fpsAdjustedSpec[ii])), 
                fpsAdjustedSpec[ii] )

        adjustedSpectrogram = barAdjustedSpec
        
        for yy in range(frameCount): # Process FFT to improve visuals
            fooFFt = adjustedSpectrogram[yy] # Load FFT for this time stamp
            fooFFt = np.abs(fooFFt) # Take absolute value
            fooFFt = np.sqrt(fooFFt) # Square root for nicer looking visual range
            # fooFFt = signal.savgol_filter(fooFFt, 10, 3) # Filter to prevent harsh edges
            adjustedSpectrogram[yy] = fooFFt

        # Find max at each spectrogram timestamp, sort maximums, and take index near the top to use as max
        specMax = np.max(adjustedSpectrogram, axis=1)
        specMax = specMax[np.where(specMax > 0.0)]
        specMax.sort()
        # print(specMax)
        divisionFactor = specMax[m.floor(len(specMax)*0.9  )] 
        print(f"   divisionFactor:{divisionFactor}")

        adjustedSpectrogram /= divisionFactor
        adjustedSpectrogram[np.where(adjustedSpectrogram > 1.0)] = 1.0

        fooPos = fooTrackDict['VID_Position']

        spectDict[fooTrack] = {
            'data':adjustedSpectrogram,
            'color':bar_color,
            'label': fooTrackDict['VID_Label'],
            'position': fooPos,
            'currFFT': deepcopy(adjustedSpectrogram[0]),
            'frameCount': frameCount,
            'barDims': _bar_dimensions(videoDims, fooPos, barCount, barGapFrac), # Order is spacing, bar width, maxHeight, xLeftEdge, yCenter
            
            # # Calculate label size
            # 'labelText': fooTrackDict['VID_Label'],
            # 'labelDims': draw.textsize(labelText, font=labelFont),
            # labelPosition = ( (vidWidth-labelWidth)/2, 0.7*vidHeight -labelHeight/2)
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
