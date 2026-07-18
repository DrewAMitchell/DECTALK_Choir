import mido


def enforceMinimumNoteDuration(starts, ends, minimumDurationTicks):
	"""Extend short notes into following rests without moving any note onset."""
	adjustedEnds = list(ends)
	minimumDurationTicks = max(0.0, float(minimumDurationTicks))
	extendedCount = 0
	constrainedCount = 0

	for index, (start, end) in enumerate(zip(starts, adjustedEnds)):
		if end - start >= minimumDurationTicks:
			continue
		if index + 1 >= len(starts):
			constrainedCount += 1
			continue

		availableEnd = starts[index + 1]
		targetEnd = min(start + minimumDurationTicks, availableEnd)
		if targetEnd > end:
			adjustedEnds[index] = targetEnd
			extendedCount += 1
		if targetEnd - start < minimumDurationTicks:
			constrainedCount += 1

	return adjustedEnds, extendedCount, constrainedCount

def loadMidiData(midiFileName, printInfo=True):
	fileTitle = midiFileName.split('/')[-1].split('.')[0]

	if printInfo: print(f"{midiFileName}  :  {fileTitle}")

	mid = mido.MidiFile(midiFileName)

	if printInfo: print(f'File mid: {mid.type}')
	
	
	if printInfo: print(f'File ticks_per_beat: {mid.ticks_per_beat}')

	notesByChannel = []
	trackNames = []

	trackTempo = 300000
	
	for channel, track in enumerate(mid.tracks):
		if printInfo: print('\nTrack {}: {}'.format(channel, track.name))
		# Match the inspector/import workflow's stable name for unnamed MIDI
		# tracks. Without this fallback, every unnamed track became
		# ``loremIpsum`` and could not be selected by Track NN settings.
		default_title = f'Track {channel:02d}'
		notesByChannel.append({
			'title': default_title,
			'tempo':trackTempo,
			'ticks_per_beat':mid.ticks_per_beat,
			'note':[],
			'velocity':[],
			'start':[],
			'end':[],
		})
		ii = 0
		active_notes = []
		active_times = []
		active_velocities = []

		currTime = 0
		for msg in track:
			currTime += msg.time

			if msg.type == 'track_name':
				if printInfo: print(f'   name:{msg}      {ii}')
				ii = 0
				trackNames.append(msg.name)
				if msg.name.strip():
					notesByChannel[channel]['title'] = msg.name
				# channel += 1
				continue
			# print(f'   {msg}')
			
			if msg.type == 'set_tempo':
				if printInfo: print(f'   tempo:{msg}      {ii}')
				notesByChannel[channel]['tempo'] = msg.tempo
				trackTempo = msg.tempo
				continue

			if msg.type not in ['note_on', 'note_off']:
				if printInfo: print(f'   {msg}      {ii}')
				ii = 0
				continue
			
			
			ii += 1
			# channel = msg.channel
			
			# while(len(notesByChannel) <= channel):  # Add new channels

			# print(f'{len(notesByChannel)} | {channel}')
			# print(notesByChannel[channel])

			# print(msg)

			is_note_on = msg.type == 'note_on' and msg.velocity > 0
			is_note_off = msg.type == 'note_off' or (
				msg.type == 'note_on' and msg.velocity == 0
			)
			if is_note_on:
				active_notes.append(msg.note)
				active_times.append(currTime)
				active_velocities.append(msg.velocity)
			elif is_note_off and msg.note in active_notes:
				noteInd = active_notes.index(msg.note)

				# if notesByChannel[channel]['title'] == 'Alto':print(f"    {msg.type}     {msg.note}     {active_times[noteInd]} -> {currTime}")
				
				notesByChannel[channel]['note'].append( msg.note )
				notesByChannel[channel]['velocity'].append(active_velocities[noteInd])
				notesByChannel[channel]['start'].append( active_times[noteInd] )
				notesByChannel[channel]['end'].append( currTime )
				

				del active_notes[noteInd]
				del active_times[noteInd]
				del active_velocities[noteInd]
				# notesByChannel[channel]['note'].append( 255 )
			elif is_note_off:
				print(f"ERROR: {msg}")
				
	return(notesByChannel)
