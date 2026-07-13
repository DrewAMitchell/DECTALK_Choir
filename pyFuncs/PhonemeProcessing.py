import cmudict
cmu_dict = cmudict.dict()

# DECTALK_Arpabet_Phonemes = ["_", "@", "&", "^", "|", "a", "A", "b", "B", "c", "C", "d", "D", "D", "E", "e", "f", "g", "G", "h", "I", "i", "J", "j", "K", "k", "L", "l", "l", "m", "M", "N", "n", "o", "O", "P", "p", "q", "Q", "r", "R", "R", "s", "S", "t", "T", "U", "u", "v", "W", "w", "x", "Y", "y", "z", "Z", "_", "ae", "dx", "ah", "ix", "aa", "ay", "b", "ir", "ao", "ch", "d", "dh", "dz", "eh", "ey", "f", "g", "nx", "hx", "ih", "iy", "jh", "ur", "er", "k", "el", "ll", "lx", "m", "or", "en", "n", "ow", "oy", "ar", "p", "eat", "tx", "r", "rr", "rx", "s", "sh", "t", "th", "uh", "uw", "v", "aw", "w", "ax", "yu", "yx", "z", "zh", ]

DECTALK_Arpabet_Conversions = {
    'hh': 'hx',
    'y':'yx',
}

DIRECT_VOWEL_PHONEMES = {
    'aa', 'ae', 'ah', 'ao', 'ar', 'aw', 'ax', 'ay',
    'eh', 'en', 'er', 'ey',
    'ih', 'ir', 'iy', 'ix',
    'or', 'ow', 'oy',
    'uh', 'ur', 'uw', 'yu',
}

DIRECT_CONSONANT_PHONEMES = {
    'b', 'ch', 'd', 'dh', 'dx', 'dz', 'f', 'g', 'hx',
    'jh', 'k', 'l', 'll', 'lx', 'm', 'n', 'ng', 'nx',
    'p', 'r', 'rr', 'rx', 's', 'sh', 't', 'th', 'tx',
    'v', 'w', 'yx', 'z', 'zh',
}

DIRECT_PHONEMES = sorted(
    DIRECT_VOWEL_PHONEMES | DIRECT_CONSONANT_PHONEMES,
    key=len,
    reverse=True,
)

Pronunciation_Overrides = {
    'dong': ['D', 'AO1', 'NG'],
    'feeling': ['F', 'IY1', 'L', 'IY0', 'NG'],
    'the': ['TH', 'IY0'],
    'to': ['T', 'UW1'],
}

LINE_TIMING_MARKER = '@line_timing'


def parseClockTimestampMs(timeText):
    timeText = timeText.strip().lower()
    if len(timeText) == 0:
        return(None)

    if timeText.endswith('ms'):
        return(round(float(timeText[:-2])))

    if timeText.endswith('s'):
        return(round(float(timeText[:-1]) * 1000))

    if ':' in timeText:
        fields = timeText.split(':')
        seconds = 0.0
        multiplier = 1.0
        for field in reversed(fields):
            if len(field) == 0:
                raise ValueError(f"Invalid timestamp field in {timeText}")
            seconds += float(field) * multiplier
            multiplier *= 60.0
        return(round(seconds * 1000))

    return(round(float(timeText) * 1000))


def parseDurationMs(durationText):
    durationText = durationText.strip().lower()
    if len(durationText) == 0:
        return(None)

    if durationText.endswith('ms'):
        return(round(float(durationText[:-2])))

    if durationText.endswith('s') or ':' in durationText:
        return(parseClockTimestampMs(durationText))

    return(round(float(durationText)))


def parseLineTimingToken(fooWord):
    fooWord = fooWord.strip()
    if len(fooWord) < 2 or fooWord[0] != '[' or fooWord[-1] != ']':
        return(None)

    timingText = fooWord[1:-1].strip()
    if len(timingText) == 0:
        raise ValueError("Empty line timing token")

    timingParts = timingText.split('|')
    if len(timingParts) > 2:
        raise ValueError(f"Invalid line timing token {fooWord}")

    startMs = parseClockTimestampMs(timingParts[0])
    durationMs = None
    if len(timingParts) == 2:
        durationMs = parseDurationMs(timingParts[1])

    if startMs is None and durationMs is None:
        raise ValueError(f"Line timing token {fooWord} did not specify a start or duration")

    return([LINE_TIMING_MARKER, startMs, durationMs])


def isDirectVowelPhoneme(phoneme):
    phoneme = phoneme.lower()
    while len(phoneme) > 0 and not phoneme[0].isalpha():
        phoneme = phoneme[1:]
    return phoneme in DIRECT_VOWEL_PHONEMES


def splitDirectPhonemeSyllable(syllable, strict=False):
    syllable = syllable.strip().lower()
    outPhonemes = []
    syllableIndex = 0

    while syllableIndex < len(syllable):
        prefix = ''
        if not syllable[syllableIndex].isalpha():
            prefix = syllable[syllableIndex]
            syllableIndex += 1
            if syllableIndex >= len(syllable):
                if strict:
                    return([])
                outPhonemes.append(prefix)
                break

        matchPhoneme = None
        remainingSyllable = syllable[syllableIndex:]
        for phoneme in DIRECT_PHONEMES:
            if remainingSyllable.startswith(phoneme):
                matchPhoneme = phoneme
                break

        if matchPhoneme is None:
            if strict:
                return([])
            outPhonemes.append(prefix + syllable[syllableIndex])
            syllableIndex += 1
        else:
            outPhonemes.append(prefix + matchPhoneme)
            syllableIndex += len(matchPhoneme)

    return(outPhonemes)


def stressDirectVowels(outPhonemes):
    stressedPhonemes = []
    for phoneme in outPhonemes:
        if isDirectVowelPhoneme(phoneme):
            stressedPhonemes.append(phoneme + '1')
        else:
            stressedPhonemes.append(phoneme)
    return(stressedPhonemes)


def convertDirectSyllableToPhonemes(fooWord):
    if len(fooWord) == 0 or fooWord[0] != '`':
        return([])

    outPhonemes = splitDirectPhonemeSyllable(fooWord[1:], strict=True)
    if len(outPhonemes) == 0 or not any(isDirectVowelPhoneme(foo) for foo in outPhonemes):
        return([])
    return(stressDirectVowels(outPhonemes))


def normalizeSungPhonemes(outPhonemes):
    for ii in range(len(outPhonemes) -1):
        phoneme = outPhonemes[ii]
        nextPhoneme = outPhonemes[ii+1]
        if phoneme[:2].upper() == 'IH' and nextPhoneme.upper() == 'NG':
            outPhonemes[ii] = 'IY' + phoneme[2:]
    return(outPhonemes)


def convertWordToPhonemes(fooWord, convertLowercase=True, DECTALK_check=True):
    if fooWord in Pronunciation_Overrides:
        outPhonemes = Pronunciation_Overrides[fooWord].copy()
    else:
        try:
            outPhonemes = cmu_dict[fooWord]
        except KeyError:
            outPhonemes = splitDirectPhonemeSyllable(fooWord, strict=True)
            if len(outPhonemes) == 0 or not any(isDirectVowelPhoneme(foo) for foo in outPhonemes):
                return([])
            outPhonemes = stressDirectVowels(outPhonemes)
            print(f"      CMU fallback direct phonemes {fooWord} -> {outPhonemes}")
        else:
            if len(outPhonemes) == 0: return([])
            outPhonemes = outPhonemes[-1].copy()

    outPhonemes = normalizeSungPhonemes(outPhonemes)

    for ii in range(len(outPhonemes)):
        if convertLowercase: outPhonemes[ii] = outPhonemes[ii].lower()

        if DECTALK_check:
            if outPhonemes[ii] in DECTALK_Arpabet_Conversions:
                print(f"      DECTALK Arpabet conversion {outPhonemes[ii]} -> {DECTALK_Arpabet_Conversions[outPhonemes[ii]]}")
                outPhonemes[ii] = DECTALK_Arpabet_Conversions[outPhonemes[ii]]

    return(outPhonemes)


def lyricsToPhonemes(lyricsFileName, printInfo=True, convertLowercase=True, DECTALK_check=True):
    outLyrics = []
    readLyrics = open(lyricsFileName, 'r')
    currentLineIndex = -1
    lyricRepetitions = 1
    for fooLine in readLyrics.readlines(): # Iterate over lines in lyric files
        currentLineIndex += 1
        lineText = fooLine.rstrip('\r\n')
        if lineText.startswith('#'): continue # Skip line if it's a comment
        splt = lineText.lower().split(' ') # Split line into words

        currentLinePhonemes = []
        for fooWord in splt:    # Iterate over words in line
            if len(fooWord) == 0: continue  # If fooWord has no characters, skip

            if fooWord[0] == '[':
                try:
                    lineTiming = parseLineTimingToken(fooWord)
                except ValueError as err:
                    print(f"ERROR: {err} in {lyricsFileName}   (line {currentLineIndex})")
                    exit()

                if lineTiming is not None:
                    outPhonemes = lineTiming
                else:
                    print(f"ERROR: Invalid line timing token \"{fooWord}\" in {lyricsFileName}   (line {currentLineIndex})")
                    exit()

            elif fooWord[0] == '!': # !X Indicates to repeat the following line X times
                try: lyricRepetitions = int(fooWord.split('!')[-1])
                except:
                    print(f"Error converting \"{fooWord}\" to repeat lyrics   (line {currentLineIndex})")
                    exit()
                continue

            elif fooWord[0] == '`':   # ` indicates to load syllable directly without modification
                outPhonemes = fooWord

            elif '*' in fooWord:    # * indicates that the word should be played for multiple notes per syllable
                splitWord = fooWord.split('*')
                if splitWord[-1].startswith('`'):
                    outPhonemes = convertDirectSyllableToPhonemes(splitWord[-1])
                else:
                    outPhonemes = convertWordToPhonemes(splitWord[-1])

                if len(outPhonemes) == 0:
                    print(f"ERROR: Unable to match \"{fooWord}\" to phonemes in {lyricsFileName}   (line {currentLineIndex})")
                    exit()

                try: outPhonemes = [int(splitWord[0])] + outPhonemes
                except:
                    print(f"Error converting \"{fooWord}\" to repeat lyrics   (line {currentLineIndex})")
                    exit()


            elif '|' in fooWord:    # X|Y|Z|lyric indicates notes per specific syllables
                splitWord = fooWord.split('|')
                if splitWord[-1].startswith('`'):
                    outPhonemes = convertDirectSyllableToPhonemes(splitWord[-1])
                else:
                    outPhonemes = convertWordToPhonemes(splitWord[-1])


                if len(outPhonemes) == 0:
                    print(f"ERROR: Unable to match \"{fooWord}\" to phonemes in {lyricsFileName}   (line {currentLineIndex})")
                    exit()

                try:
                    outPhonemes = [[int(foo) for foo in splitWord[:-1]]] + outPhonemes
                except:
                    print(f"ERROR: Unable to converting \"{fooWord}\" to phonemes in {lyricsFileName}   (line {currentLineIndex})")

            else:   # No special case, convert directly
                outPhonemes = convertWordToPhonemes(fooWord)

                if len(outPhonemes) == 0:
                    print(f"ERROR: Unable to match \"{fooWord}\" to phonemes in {lyricsFileName}   (line {currentLineIndex})")
                    exit()

            currentLinePhonemes.append(outPhonemes)
            if printInfo: print(f"{fooWord} -> {currentLinePhonemes[-1]}")

        if printInfo: print('')
        currentLinePhonemes.append(['\n'])

        for ii in range(lyricRepetitions):
            outLyrics = outLyrics + currentLinePhonemes

        lyricRepetitions = 1

    return(outLyrics)


def savePhonemesToFile(phonemes, fileName):
    outFile = open(fileName, 'w')

    for foo in phonemes:
        for bar in foo:
            outFile.write(f"{bar} ")
        outFile.write('     ')

    outFile.close()



def loadPhonemesFromFile(fileName):
    inFile = open(fileName, 'r')

    phonemes = []
    for readline in inFile.readlines():
        lineSplt = readline.split('      ')[1:]

        if len(lineSplt) == 1:
            phonemes.append(['\n'])
        else:
            for foo in lineSplt[:-1]:
                phonemes.append(foo.split(' '))

    inFile.close()
