# This is the primary compilation script for this whole project
# Specify song title in command line, should be name of folder in /songs

# Convert lyrics and midi to playable demo
import sys
import os
import shutil
import time
import math as m

# Make sure song is specified
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
	print(f"songs/{songTitle}/settings.yaml not loaded")
	exit()

# Constants loaded from settings.yaml
if not 'noteOffset' in settings_yaml: noteOffset = -48
else: noteOffset = settings_yaml['noteOffset']

if not 'consonantFractionTarget' in settings_yaml: consonantFractionTarget = 0.15
else: consonantFractionTarget = settings_yaml['consonantFractionTarget']

if not 'consonantMinMs' in settings_yaml: consonantMinMs = 5
else: consonantMinMs = settings_yaml['consonantMinMs']

if not 'consonantMaxMs' in settings_yaml: consonantMaxMs = 75
else: consonantMaxMs = settings_yaml['consonantMaxMs']

if not 'minDectalkPitch' in settings_yaml: minDectalkPitch = 0
else: minDectalkPitch = settings_yaml['minDectalkPitch']

if not 'gapMendMs' in settings_yaml: gapMendMs = 0.0
else: gapMendMs = float(settings_yaml['gapMendMs'])

def roundBoostSemitones(boost):
	if boost >= 0:
		return int(m.floor(boost + 0.5))
	return int(m.ceil(boost - 0.5))

def getDectalkPitch(noteValue, octaveBoost):
	noteVal = round(noteValue - octaveBoost)
	while noteVal < minDectalkPitch:
		noteVal += 12
	return noteVal

def getOctavePitchShift(pitchMin):
	if pitchMin >= minDectalkPitch:
		return 0
	return int(m.ceil((minDectalkPitch-pitchMin)/12)*12)

def resetOutputDir(outputDir):
	for ii in range(10):
		try:
			if os.path.isdir(outputDir):
				shutil.rmtree(outputDir)
			os.makedirs(outputDir, exist_ok = True)
			return
		except PermissionError:
			time.sleep(0.25)

	print(f"WARNING: Unable to fully clear {outputDir}; continuing with existing files")
	os.makedirs(outputDir, exist_ok = True)

# Load default settings for track settings
for fooTrack in settings_yaml['Tracks']:
	trackDict = settings_yaml['Tracks'][fooTrack]
	if 'LYRICS_FILENAME' not in trackDict: trackDict['LYRICS_FILENAME'] = fooTrack
	if 'TRACK_FILENAME' not in trackDict: trackDict['TRACK_FILENAME'] = fooTrack
	if 'DEC_SETUP' not in trackDict: trackDict['DEC_SETUP'] = ''
	if 'VOLUME_ADJUST_DB' not in trackDict: trackDict['VOLUME_ADJUST_DB'] = 0.0
	if 'PITCH_SHIFT' not in trackDict: trackDict['PITCH_SHIFT'] = 0
	if 'OCTAVE_BOOST' not in trackDict: trackDict['OCTAVE_BOOST'] = 0
	if 'GAP_MEND_MS' not in trackDict: trackDict['GAP_MEND_MS'] = gapMendMs
	if 'PITCH_VOLUME_BOOST_START' not in trackDict: trackDict['PITCH_VOLUME_BOOST_START'] = 0
	if 'PITCH_VOLUME_BOOST_DB_PER_SEMITONE' not in trackDict: trackDict['PITCH_VOLUME_BOOST_DB_PER_SEMITONE'] = 0.0
	if 'PITCH_VOLUME_BOOST_MAX_DB' not in trackDict: trackDict['PITCH_VOLUME_BOOST_MAX_DB'] = 6.0
	if 'SEGMENT_NORMALIZE_PITCH_START' not in trackDict: trackDict['SEGMENT_NORMALIZE_PITCH_START'] = trackDict['PITCH_VOLUME_BOOST_START']
	if 'SEGMENT_NORMALIZE_TARGET_DBFS' not in trackDict: trackDict['SEGMENT_NORMALIZE_TARGET_DBFS'] = -18.0
	if 'SEGMENT_NORMALIZE_MAX_BOOST_DB' not in trackDict: trackDict['SEGMENT_NORMALIZE_MAX_BOOST_DB'] = 0.0
	if 'PITCH_WRAP_SHIFT' not in trackDict: trackDict['PITCH_WRAP_SHIFT'] = None

	foo_OCTAVE_BOOST = float(trackDict['OCTAVE_BOOST'])
	if foo_OCTAVE_BOOST != int(foo_OCTAVE_BOOST):
		roundedBoost = roundBoostSemitones(foo_OCTAVE_BOOST)
		print(f"WARNING: {fooTrack} OCTAVE_BOOST {foo_OCTAVE_BOOST} rounded to {roundedBoost}; fractional boosts cannot stay in tune with integer DECTALK pitches")
		trackDict['OCTAVE_BOOST'] = roundedBoost
	else:
		trackDict['OCTAVE_BOOST'] = int(foo_OCTAVE_BOOST)


def getTrackSourceName(outputPartName):
	return settings_yaml['Tracks'][outputPartName]['TRACK_FILENAME']


gapMendMsByMidiPart = {}
for outputPartName in settings_yaml['Tracks']:
	trackDict = settings_yaml['Tracks'][outputPartName]
	sourceName = trackDict['TRACK_FILENAME']
	gapMendMsByMidiPart[sourceName] = max(
		gapMendMsByMidiPart.get(sourceName, gapMendMs),
		float(trackDict['GAP_MEND_MS'])
	)



# Load Lyrics and convert to phonemes
print(f"Converting tracks to phonemes ")
import pyFuncs.PhonemeProcessing as pp
phonemeSet = {}
lyricsList = [foo.split('.')[0] for foo in os.listdir(f"songs/{songTitle}/lyrics")]
for trackName in settings_yaml['Tracks']:
	lyricsFileStem = settings_yaml['Tracks'][trackName]['LYRICS_FILENAME']
	if lyricsFileStem not in lyricsList: continue
	print(f"   Converting /lyrics/{lyricsFileStem}.txt for  {trackName}")
	lyricFileName = f"songs/{songTitle}/lyrics/{lyricsFileStem}.txt"
	phonemes = pp.lyricsToPhonemes(lyricFileName, DECTALK_check=True, printInfo=False)
	print(phonemes)
	phonemeSet[trackName] = phonemes



# for foo in phonemeSet:
#     print(f"\n\n\n{foo}:")
#     for bar in phonemeSet[foo]:
#         print(f"   {bar}")



# Get name of midi file
midiFileName = ''
for foo in os.listdir(f"songs/{songTitle}"):
	if foo.split('.')[-1] == 'mid':
		midiFileName = f"songs/{songTitle}/{foo}"
		break

# Catch if no midi file
if midiFileName == '':
	print("No midi file found")
	exit()



# Load MIDI data
import pyFuncs.MidiProcessing as pymidi

midiData = pymidi.loadMidiData(midiFileName)#, printInfo=False)

# Convert midi data to notes and durations
noteSet = {}
for fooMidi in midiData:
	midiPartName = fooMidi['title']

	# Load tempo for track
	if 'tempoEmergencyOverride' in settings_yaml:
		tempo_ms = settings_yaml['tempoEmergencyOverride']
	else:
		tempo_ms = fooMidi['tempo']/1000

	ticksPerBeat = fooMidi['ticks_per_beat']
	trackGapMendMs = gapMendMsByMidiPart.get(midiPartName, gapMendMs)

	# Prevent overlapping notes
	for ii in range(len(fooMidi['note']) -1):
		if fooMidi['end'][ii] >= fooMidi['start'][ii+1]:
			fooMidi['end'][ii] = fooMidi['start'][ii+1]


	# Convert midi track into notes array
	# Notes values are (note index, velocity, duration, start time)
	notes = []
	prevNoteTime = 0
	for ii in range(len(fooMidi['note'])):
		if fooMidi['start'][ii] > prevNoteTime:
			gapDurationMs = (fooMidi['start'][ii] -prevNoteTime)*tempo_ms/ticksPerBeat
			if trackGapMendMs > 0 and gapDurationMs <= trackGapMendMs and len(notes) > 0 and notes[-1][0] != -1:
				notes[-1][2] += gapDurationMs
			else:
				notes.append([-1, 0, gapDurationMs, fooMidi['start'][ii]*tempo_ms/ticksPerBeat])

		notes.append([fooMidi['note'][ii], fooMidi['velocity'][ii],   (fooMidi['end'][ii] -fooMidi['start'][ii])*tempo_ms/ticksPerBeat,   fooMidi['start'][ii]*tempo_ms/ticksPerBeat] )

		prevNoteTime = fooMidi['end'][ii]

	# for foo in notes[:10]:print(f"   {foo}")

	if len(notes) > 0:
		noteSet[midiPartName] = notes
		justPitches = list(zip(*notes))[0]
		print(f"\n{midiPartName}:\n   tempo_ms:{tempo_ms}\n   gapMendMs:{trackGapMendMs}\n   Range:{min((foo for foo in justPitches if foo >= 0))} -> {max(justPitches)}")
	else:
		print(f"MIDI Track {midiPartName} has no notes data, ignoring")

# exit()







# Check and print part matching between configured output names, lyrics, and MIDI source tracks.
midiPartNames = [foo for foo in noteSet]
phonPartNames = [foo for foo in phonemeSet]
partNamesToOutput = []

for outputPartName in settings_yaml['Tracks']:
	sourcePartName = getTrackSourceName(outputPartName)
	if outputPartName in phonemeSet and sourcePartName in noteSet:
		partNamesToOutput.append(outputPartName)
		if sourcePartName in midiPartNames:
			midiPartNames.remove(sourcePartName)
		if outputPartName in phonPartNames:
			phonPartNames.remove(outputPartName)

print("\n")
print(f"Parts with both words and MIDI:{partNamesToOutput}")
print(f"Parts with just MIDI:{midiPartNames}")
print(f"Parts with just words:{phonPartNames}")

pitchWrapShiftByPart = {}
for fooPartName in partNamesToOutput:
	foo_OCTAVE_BOOST = settings_yaml['Tracks'][fooPartName]['OCTAVE_BOOST']
	foo_PITCH_SHIFT = int(settings_yaml['Tracks'][fooPartName]['PITCH_SHIFT'])
	sourcePartName = getTrackSourceName(fooPartName)
	partPitches = [foo[0]+noteOffset+foo_PITCH_SHIFT-foo_OCTAVE_BOOST for foo in noteSet[sourcePartName] if foo[0] >= 0]
	if len(partPitches) == 0:
		pitchWrapShiftByPart[fooPartName] = 0
		continue

	trackDict = settings_yaml['Tracks'][fooPartName]
	if trackDict['PITCH_WRAP_SHIFT'] is None:
		pitchWrapShift = getOctavePitchShift(min(partPitches))
	else:
		pitchWrapShift = int(trackDict['PITCH_WRAP_SHIFT'])

	pitchWrapShiftByPart[fooPartName] = pitchWrapShift
	if pitchWrapShift != 0:
		print(f"{fooPartName} pitch wrap shift:{pitchWrapShift:+} semitones   Range:{min(partPitches)} -> {max(partPitches)} becomes {min(partPitches)+pitchWrapShift} -> {max(partPitches)+pitchWrapShift}")

	finalDectalkPitches = [
		getDectalkPitch(foo[0]+noteOffset+foo_PITCH_SHIFT+pitchWrapShift, foo_OCTAVE_BOOST)
		for foo in noteSet[sourcePartName]
		if foo[0] >= 0
	]
	trackDict = settings_yaml['Tracks'][fooPartName]
	pitchSpanDetails = []
	if float(trackDict['PITCH_VOLUME_BOOST_DB_PER_SEMITONE']) > 0:
		pitchSpanDetails.append(f"volume boost starts at {trackDict['PITCH_VOLUME_BOOST_START']}")
	if float(trackDict['SEGMENT_NORMALIZE_MAX_BOOST_DB']) > 0:
		pitchSpanDetails.append(f"segment normalize starts at {trackDict['SEGMENT_NORMALIZE_PITCH_START']}")
	pitchSpanDetailsText = ''
	if len(pitchSpanDetails) > 0:
		pitchSpanDetailsText = f"   ({', '.join(pitchSpanDetails)})"
	print(f"{fooPartName} DECTALK pitch span:{min(finalDectalkPitches)} -> {max(finalDectalkPitches)}{pitchSpanDetailsText}")



# # Add defaults to settings.yaml if not found
# for fooName in partNamesToOutput:
#     if not fooName in settings_yaml:
#         print(f"{fooName} NOT FOUND in settings.yaml, adding defaults")
#         settings_yaml[fooName] = {
#             'DEC_SETUP': '',
#             'VOLUME_ADJUST_DB': 1.0
#         }

#     else:
#         if not 'DEC_SETUP' in settings_yaml[fooName]:
#             print(f"DEC_SETUP NOT FOUND for {fooName} in settings.yaml, adding default")
#             settings_yaml[fooName]['DEC_SETUP'] = ''
#         if not 'VOLUME_ADJUST_DB' in settings_yaml[fooName]:
#             print(f"VOLUME_ADJUST_DB NOT FOUND for {fooName} in settings.yaml, adding default")
#             settings_yaml[fooName]['VOLUME_ADJUST_DB'] = 1.0



# Iterate through parts, saving each line by line to dict
compiledLyrics = {}
for fooPartName in partNamesToOutput:
	# # Actually write output
	# lyricFileName = f"outputs/{songTitle}/phonemes/{fooPartName}.txt"
	fooPhonemes = phonemeSet[fooPartName]
	fooNotes = noteSet[getTrackSourceName(fooPartName)]
	fooCompiledLyrics = [[]]

	# outputFile = open(lyricFileName, 'w')
	# # outputFile.write("[:phoneme arpabet speak on]\n[")
	# outputFile.write("[:phone arpa on][:np][")

	lyricIndex = 0
	noteIndex = 0
	while lyricIndex < len(fooPhonemes) and noteIndex < len(fooNotes): # match notes  to phonemes until one of them runs out
		# If lyric is newline
		if fooPhonemes[lyricIndex][0] == '\n':
			if len(fooCompiledLyrics[-1]) > 0: fooCompiledLyrics.append([]) # Go to new complied line on newline character
			lyricIndex += 1
			continue

		notesInWord = 1


		if fooPhonemes[lyricIndex][0] == '`': # If syllable was input directly
			symbolsToSing = pp.splitDirectPhonemeSyllable(fooPhonemes[lyricIndex][1:])
			symbolIsVowel = [1 if pp.isDirectVowelPhoneme(foo) else 0 for foo in symbolsToSing]
		else:
			symbolsToSing = []
			symbolIsVowel = []

			if type(fooPhonemes[lyricIndex][0]) == list: # X|Y|Z|Lyric syntax, specify number of beats for each
				vowelLens = fooPhonemes[lyricIndex][0]
				notesInWord = sum(vowelLens)
				currVowel = 0
				# Load symbols and detect if they are vowels or not
				for fooPhoneme in fooPhonemes[lyricIndex][1:]:
					if fooPhoneme[-1].isnumeric(): # If last character in syllable is vowel, symbol is a vowel
						if currVowel >= len(vowelLens): break # Break if attempting to pronounce vowel but no more beat counts are specified

						for ii in range(vowelLens[currVowel]): # Append once for every vowel
							symbolsToSing.append(fooPhoneme[:-1]) # Drop number at end of vowel
							symbolIsVowel.append(1)

						currVowel += 1
					else:
						symbolsToSing.append(fooPhoneme)
						symbolIsVowel.append(0)
			else:
				# If playing multiple notes over course of word (X*Lyric syntax), first phoneme will be an int
				if type( fooPhonemes[lyricIndex][0] ) == int:
					notesInWord = fooPhonemes[lyricIndex][0]
					fooPhonemes[lyricIndex] = fooPhonemes[lyricIndex][1:] # Remove X* phoneme from start

				# Load symbols and detect if they are vowels or not
				for fooPhoneme in fooPhonemes[lyricIndex]:
					if fooPhoneme[-1].isnumeric(): # If last character in syllable is vowel, symbol is a vowel
						symbolsToSing.append(fooPhoneme[:-1]) # Drop number at end of vowel
						symbolIsVowel.append(1)
					else:
						symbolsToSing.append(fooPhoneme)
						symbolIsVowel.append(0)

		# Iterate symbolsToSing & symbolIsVowel, matching notes to lyrics
		vowelsRemaining = sum(symbolIsVowel)
		symbolSingIndex = 0
		while symbolSingIndex < len(symbolsToSing):
			if noteIndex >= len(fooNotes): break
			# Load next note to be played
			noteValue = fooNotes[noteIndex][0]
			noteVelocity = fooNotes[noteIndex][1]
			noteDuration = fooNotes[noteIndex][2]
			noteStart = fooNotes[noteIndex][3]
			noteIndex += 1

			# If note is a rest, write pause and load next note
			if noteValue == -1:
				if len(fooCompiledLyrics[-1]) > 0: fooCompiledLyrics[-1].append( ('_', round(noteDuration), 0, 0) ) # Save current note as pause if there are compiled lyrics
				noteValue = fooNotes[noteIndex][0]
				noteVelocity = fooNotes[noteIndex][1]
				noteDuration = fooNotes[noteIndex][2]
				noteStart = fooNotes[noteIndex][3]
				noteIndex += 1

			# print(f"NOTE:{noteValue}->{noteValue+noteOffset}   {round(noteDuration,3)}")
			noteValue += noteOffset
			noteValue += int(settings_yaml['Tracks'][fooPartName]['PITCH_SHIFT'])
			noteValue += pitchWrapShiftByPart.get(fooPartName, 0)



			# Select one of three situations on how to keep iterating through word
			if notesInWord == 1: # Only one note remains, play remainder of phonemes on it
				# print("LAST NOTE")
				symbolIsVowel_subset = symbolIsVowel[symbolSingIndex:]
				symbolsToSing_subset = symbolsToSing[symbolSingIndex:]
				symbolSingIndex = len(symbolsToSing)
				notesInWord = 0

			elif sum(symbolIsVowel[symbolSingIndex:]) > 1: # Multiple notes and vowels remaining, stay on note to next vowel
				# print("ITERATING THROUGH")
				# Find next vowel
				nextVowelIndex = symbolSingIndex+1
				while not symbolIsVowel[nextVowelIndex]: nextVowelIndex += 1
				symbolIsVowel_subset = symbolIsVowel[symbolSingIndex:(nextVowelIndex+1)]
				symbolsToSing_subset = symbolsToSing[symbolSingIndex:(nextVowelIndex+1)]
				symbolSingIndex = nextVowelIndex
				notesInWord -= 1

			else: # Only one vowel remains but multiple notes should be played, play all on vowel
				# print("LAST VOWEL")
				# Find next vowel
				nextVowelIndex = symbolSingIndex
				while not symbolIsVowel[nextVowelIndex]: nextVowelIndex += 1
				symbolIsVowel_subset = symbolIsVowel[symbolSingIndex:(nextVowelIndex+1)]
				symbolsToSing_subset = symbolsToSing[symbolSingIndex:(nextVowelIndex+1)]
				symbolSingIndex = nextVowelIndex
				notesInWord -= 1



			# Calculate durations for each phoneme
			vowelCount = sum(symbolIsVowel_subset)
			consonantCount = len(symbolIsVowel_subset) - sum(symbolIsVowel_subset)

			if consonantCount == 0 or vowelCount == 0: # If word does not have vowels or consonants catch divide by 0 error
				consonantDuration = round(noteDuration * consonantCount / (consonantCount + vowelCount))
				consonantFraction = consonantDuration
				vowelDuration = round(noteDuration * vowelCount / (consonantCount + vowelCount))
			else:
				consonantFraction = consonantFractionTarget - pow(consonantFractionTarget, consonantCount+1)
				consonantDuration = round(noteDuration * consonantFraction / consonantCount)
				vowelDuration = round(noteDuration * (1-consonantFraction) / vowelCount)

			if consonantDuration < consonantMinMs:
				consonantDuration = consonantMinMs
				vowelDuration = round( (noteDuration -consonantCount*consonantDuration) / vowelCount)

			if consonantDuration > consonantMaxMs:
				consonantDuration = consonantMaxMs
				vowelDuration = round( (noteDuration -consonantCount*consonantDuration) / vowelCount)


			if consonantDuration < 0 or vowelDuration < 0:
				print(f"Too fast to pronounce {symbolsToSing_subset[ii]} in lyric \"{fooPhonemes[lyricIndex]}\"")
				continue


			# Actually save phonemes to array
			for ii in range(len(symbolsToSing_subset)):
				if symbolIsVowel_subset[ii]: outputSet = ( symbolsToSing_subset[ii], vowelDuration, noteValue, noteVelocity )
				else: outputSet = ( symbolsToSing_subset[ii], consonantDuration, noteValue, noteVelocity )

				if len(fooCompiledLyrics[-1]) == 0: fooCompiledLyrics[-1].append(round(noteStart)) # Save start time of each compiled lyric set
				fooCompiledLyrics[-1].append(outputSet) # Save to output

		lyricIndex += 1
		# fooCompiledLyrics[-1].append(' ')


	if fooCompiledLyrics[-1] == []: fooCompiledLyrics = fooCompiledLyrics[:-1] # Catch if there is an extra unfilled line at end of lyrics

	compiledLyrics[fooPartName] = fooCompiledLyrics

# Display lyrics over data
if '-plt' in sys.argv[1]:
	from PIL import Image, ImageDraw, ImageFont
	os.makedirs(f"outputs/{songTitle}/_vis", exist_ok = True) # Folder for output files

	colorSet = [(147, 181, 198), (221, 237, 170), (240, 207, 101), (215, 129, 106), (189, 79, 108), ]
	yPerNote = 120
	xPerMs = 0.8

	# notes.append([fooMidi['note'][ii], fooMidi['velocity'][ii],   (fooMidi['end'][ii] -fooMidi['start'][ii])*tempo_ms/128,   fooMidi['start'][ii]*tempo_ms/128] )
	maxNote = -1
	minNote = 99999
	endTime = 0
	for fooPartName in partNamesToOutput:
		foo_DEC_SETUP = settings_yaml['Tracks'][fooPartName]['DEC_SETUP']
		foo_OCTAVE_BOOST = settings_yaml['Tracks'][fooPartName]['OCTAVE_BOOST']

		for fooLine in compiledLyrics[fooPartName]:

			# Display Phoneme
			for fooPhen in fooLine[1:]:
				if fooPhen == ' ': continue

				noteLen = round(fooPhen[1]*pow(2, foo_OCTAVE_BOOST/12))
				noteVal = getDectalkPitch(fooPhen[2], foo_OCTAVE_BOOST)

				if noteVal > maxNote:
					maxNote = noteVal
				if noteVal > -1 and noteVal < minNote:
					minNote = noteVal

				endTime += noteLen


	print(f"maxNote:{maxNote}")
	print(f"minNote:{minNote}")
	print(f"endTime:{endTime}")
	imageDims = (m.ceil(endTime*xPerMs), m.ceil((maxNote-minNote)*yPerNote))
	print(f"imageDims:{imageDims}")

	# Display each track
	ii = -1
	for fooPartName in partNamesToOutput:
		backColor = (0, 0, 0)
		outImg = Image.new("RGBA", imageDims, backColor)
		draw = ImageDraw.Draw(outImg)
		labelFont = ImageFont.truetype('pyFuncs/fonts/NexaText-Trial-Light.ttf', 80)

		ii += 1
		fooCol = colorSet[ii%len(colorSet)]
		fooColAlpha = (fooCol[0], fooCol[1], fooCol[2], 100)
		foo_DEC_SETUP = settings_yaml['Tracks'][fooPartName]['DEC_SETUP']
		foo_OCTAVE_BOOST = settings_yaml['Tracks'][fooPartName]['OCTAVE_BOOST']

		print(f"{fooPartName} phoneme display   ii:{ii}   col:{fooCol}")
		for fooLine in compiledLyrics[fooPartName]:
			startTime = fooLine[0]

			# Display Phoneme
			for fooPhen in fooLine[1:]:
				if fooPhen == ' ': continue
				# labelText = f"{fooPhen[0]}<{round(fooPhen[1]*pow(2, foo_OCTAVE_BOOST/12))},{round(fooPhen[2]-foo_OCTAVE_BOOST)}>"

				noteLen = round(fooPhen[1]*pow(2, foo_OCTAVE_BOOST/12))
				noteVal = getDectalkPitch(fooPhen[2], foo_OCTAVE_BOOST)

				labelText = f"{fooPhen[0]}"
				labelDims = labelFont.getbbox(labelText)

				notePos = maxNote-noteVal

				draw.rectangle(
					((round(startTime*xPerMs), (notePos)*yPerNote),
					(round((startTime+noteLen)*xPerMs), (notePos +1)*yPerNote)),
					fooColAlpha,
				)

				# print((maxNote -noteVal)*yPerNote)

				draw.text(
					(round((startTime+noteLen/2 -labelDims[0]/2)*xPerMs), (notePos)*yPerNote),
					labelText,
					fooCol,
					labelFont)

				startTime += noteLen

		outImg.save(f"outputs/{songTitle}/_vis/phonemePlot_{fooPartName}.png")



# Save text files of partial tracks and generate .wavs
print(f"\n\nGenerating partial audio files")
procSet = [] # Save all currently running processes, to make sure all finish before moving on
if True:
	import subprocess as sp

	# Output files
	os.makedirs(f"outputs/{songTitle}/", exist_ok = True) # Folder for output files
	outputTracksDir = f"outputs/{songTitle}/_tracks"
	resetOutputDir(outputTracksDir) # Folder for output audio tracks
	os.makedirs(f"outputs/{songTitle}/_finished", exist_ok = True) # Folder for final mixed outputs

	# Iterate over each track and save
	for fooPartName in partNamesToOutput:
		foo_DEC_SETUP = settings_yaml['Tracks'][fooPartName]['DEC_SETUP']
		foo_OCTAVE_BOOST = settings_yaml['Tracks'][fooPartName]['OCTAVE_BOOST']
		# if fooPartName != 'Tenor': continue

		partialOutputDir = f"outputs/{songTitle}/{fooPartName}"
		resetOutputDir(partialOutputDir) # Save partial tracks

		# Generate partial .txt files for running DECtalk
		print(f"{fooPartName} Partial txt files")
		for fooLine in compiledLyrics[fooPartName]:
			startTime = fooLine[0]
			partialTxtFile = f"outputs/{songTitle}/{fooPartName}/{startTime}.txt"

			# Write partial text file
			partialTxtFile = open(partialTxtFile, 'w')
			partialTxtFile.write(f"[:phoneme arpabet speak on]{foo_DEC_SETUP}[")
			for fooPhen in fooLine[1:]:
				if fooPhen == ' ':
					partialTxtFile.write(' ')
				else:
					noteLen = round(fooPhen[1]*pow(2, foo_OCTAVE_BOOST/12))
					noteVal = getDectalkPitch(fooPhen[2], foo_OCTAVE_BOOST)
					partialTxtFile.write(f"{fooPhen[0]}<{noteLen},{noteVal}>")

			partialTxtFile.write("]")
			partialTxtFile.close()

		# Generate partial .wav files by calling DECtalk on each file
		print(f"{fooPartName} Partial wav files")
		for fooLine in compiledLyrics[fooPartName]:
			startTime = fooLine[0]
			partialTxtFile = f"outputs/{songTitle}/{fooPartName}/{startTime}.txt"
			# partialTxtFile = open(partialTxtFileName, 'r')

			outputWav = f"outputs/{songTitle}/{fooPartName}/{startTime}.wav"
			partialTxtInput = open(partialTxtFile, 'rb')
			DEC_proc = sp.Popen([f".{os.sep}say.exe", "-w", outputWav], stdin=partialTxtInput) # Finally actual run DECtalk! Opens a bunch of processes to run every file in parallel
			partialTxtInput.close()
			procSet.append(DEC_proc)

			# if fooPartName == "Bass": print(f"./say.exe -w {outputWav} < {partialTxtFileName}")

# Wait for all of the DECtalk programs to exit
ii=0
while len(procSet) > 0:
	if procSet[ii].poll() != None:
		procSet.pop(ii)
	else:
		ii += 1

	if ii >= len(procSet):
		ii = 0
		print(f"Waiting on say.exe processes to finish, {len(procSet)} remaing")
		time.sleep(0.5)


# Mix each partial wav file
print(f"\n\nMixing partial wav files")


from pydub import AudioSegment
import pyrubberband as pyrb


def applyPitchVolumeBoost(audioSegment, fooLine, octaveBoost, trackDict):
	boostPerSemitone = float(trackDict['PITCH_VOLUME_BOOST_DB_PER_SEMITONE'])
	if boostPerSemitone <= 0:
		return audioSegment

	boostStart = float(trackDict['PITCH_VOLUME_BOOST_START'])
	boostMaxDb = float(trackDict['PITCH_VOLUME_BOOST_MAX_DB'])
	boostedAudio = AudioSegment.empty()
	cursorMs = 0

	for fooPhen in fooLine[1:]:
		if fooPhen == ' ':
			continue

		segmentLen = round(fooPhen[1]*pow(2, octaveBoost/12))
		segment = audioSegment[cursorMs:cursorMs+segmentLen]
		cursorMs += segmentLen

		if fooPhen[0] != '_':
			noteVal = getDectalkPitch(fooPhen[2], octaveBoost)
			boostDb = min(boostMaxDb, max(0.0, (noteVal - boostStart)*boostPerSemitone))
			if boostDb > 0:
				segment += boostDb

		boostedAudio += segment

	if cursorMs < len(audioSegment):
		boostedAudio += audioSegment[cursorMs:]

	return boostedAudio


def applySegmentNormalize(audioSegment, fooLine, octaveBoost, trackDict):
	maxBoostDb = float(trackDict['SEGMENT_NORMALIZE_MAX_BOOST_DB'])
	if maxBoostDb <= 0:
		return audioSegment

	normalizePitchStart = float(trackDict['SEGMENT_NORMALIZE_PITCH_START'])
	targetDbfs = float(trackDict['SEGMENT_NORMALIZE_TARGET_DBFS'])
	peakCeilingDbfs = -1.0
	normalizedAudio = AudioSegment.empty()
	cursorMs = 0

	for fooPhen in fooLine[1:]:
		if fooPhen == ' ':
			continue

		segmentLen = round(fooPhen[1]*pow(2, octaveBoost/12))
		segment = audioSegment[cursorMs:cursorMs+segmentLen]
		cursorMs += segmentLen

		if fooPhen[0] != '_' and len(segment) > 0:
			noteVal = getDectalkPitch(fooPhen[2], octaveBoost)
			if noteVal >= normalizePitchStart and m.isfinite(segment.dBFS):
				boostDb = min(maxBoostDb, max(0.0, targetDbfs - segment.dBFS))
				if m.isfinite(segment.max_dBFS):
					boostDb = min(boostDb, peakCeilingDbfs - segment.max_dBFS)
				if boostDb > 0:
					segment += boostDb

		normalizedAudio += segment

	if cursorMs < len(audioSegment):
		normalizedAudio += audioSegment[cursorMs:]

	return normalizedAudio


outputAudioDict = {}
for fooPartName in partNamesToOutput:
	foo_OCTAVE_BOOST = settings_yaml['Tracks'][fooPartName]['OCTAVE_BOOST']
	firstNote = compiledLyrics[fooPartName][0][0] # Get time of first note
	outputAudio = AudioSegment.silent((firstNote + 1000)) # Init audio output as silence to fill up until the start of the audio

	for fooLine in compiledLyrics[fooPartName]:
		# Get average velocities of notes in this line to adjust playback volume
		velocities = [foo[3] for foo in fooLine[1:] if type(foo) == tuple and foo[0] != '_']
		meanVelocity = (sum(velocities)/len(velocities)/127 -0.5)*10

		startTime = fooLine[0]
		readWavFileName = f"outputs/{songTitle}/{fooPartName}/{startTime}.wav"

		# try:
		nextAudio = AudioSegment.from_file(readWavFileName) +meanVelocity +settings_yaml['Tracks'][fooPartName]['VOLUME_ADJUST_DB']
		nextAudio = applyPitchVolumeBoost(nextAudio, fooLine, foo_OCTAVE_BOOST, settings_yaml['Tracks'][fooPartName])
		nextAudio = applySegmentNormalize(nextAudio, fooLine, foo_OCTAVE_BOOST, settings_yaml['Tracks'][fooPartName])

		if foo_OCTAVE_BOOST != 0: #  Multiply playback speed by OCTAVE_BOOST
			initRate = nextAudio.frame_rate
			new_sample_rate = int(initRate * pow(2, foo_OCTAVE_BOOST/12))
			print(f"initRate:{initRate}     new_sample_rate:{new_sample_rate}")
			nextAudio = nextAudio._spawn(nextAudio.raw_data, overrides={'frame_rate': new_sample_rate})
			nextAudio = nextAudio.set_frame_rate(initRate)

		outputAudio = (AudioSegment.silent((startTime +1000)) + nextAudio).overlay(outputAudio)
		# except:
		#     print(f"ERROR READING {readWavFileName}, LINE NOT INCLUDED")

		# print(f"{fooPartName}:{startTime}")


		# if len(outputAudio) < startTime: # Need to add some silence
		#     print(f"<")
		#     outputAudio = outputAudio + AudioSegment.silent(startTime-len(outputAudio))
		#     outputAudio = outputAudio + nextAudio

		# elif len(outputAudio) == startTime: # Lines up exactly, can play audio immediately
		#     print(f"==")
		#     outputAudio = outputAudio + nextAudio

		# elif len(outputAudio) > startTime: # Audio overlaps, overlay
		#     print(f">")
		#     outputAudio = outputAudio[:startTime] + outputAudio[startTime:].overlay(nextAudio)

	outputAudioDict[fooPartName] = outputAudio

audioLenth = max( [outputAudioDict[fooPartName].duration_seconds for fooPartName in partNamesToOutput] )
targetAudioLengthMs = round(audioLenth*1000)

print(f"audioLenth:{audioLenth}")

# Export equal-length track stems
for fooPartName in partNamesToOutput:
	trackAudio = outputAudioDict[fooPartName]
	if len(trackAudio) < targetAudioLengthMs:
		trackAudio += AudioSegment.silent(targetAudioLengthMs-len(trackAudio))
	elif len(trackAudio) > targetAudioLengthMs:
		trackAudio = trackAudio[:targetAudioLengthMs]
	outputAudioDict[fooPartName] = trackAudio

	print(f"Exporting:   outputs/{songTitle}/_tracks/{fooPartName}.wav")
	trackAudio.export(f"outputs/{songTitle}/_tracks/{fooPartName}.wav", format='wav') #export mixed  audio file

# Final mix
outputAudio = AudioSegment.silent(targetAudioLengthMs)
for fooPartName in partNamesToOutput:
	readWavFileName = f"outputs/{songTitle}/_tracks/{fooPartName}.wav"
	trackAudio = AudioSegment.from_file(readWavFileName)
	outputAudio = outputAudio.overlay(trackAudio)

	# outputAudio.overlay(outputAudioDict[fooPartName])
	# print(outputAudioDict[fooPartName])

print(f"Exporting:   outputs/{songTitle}/_finished/{songTitle}.wav")
outputAudio.export(f"outputs/{songTitle}/_finished/{songTitle}.wav", format='wav')

# Generate spectrogram visualization if -vis tag is included
if '-vis' in sys.argv[1]:
	import subprocess as sp
	sp.run(f"python3 generateSpectrograms.py {songTitle}", shell=True)



if '-play' in sys.argv[1]:
	# from playsound import playsound
	# playsound(f"outputs/{songTitle}/_finished/{songTitle}.wav")

	from pydub.playback import play
	play(outputAudio)
