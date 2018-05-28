#!/usr/bin/env python

import requests
import sys
import time
import json
import os
import subprocess
import datetime

LogLevel = 0
PrintLevel = 1
LOG_LEVEL_ERROR = 0
LOG_LEVEL_TRACE = 1

def Log(log, message, level):
	if(level <= PrintLevel):
		print(message + '\n')

	if(level <= LogLevel):
		logEntry = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ' - ' + message + '\n'
		log.write(logEntry)
#end Log

def CheckPrinting(printerApiUrl, headers, tempDetectEnabled, tempThreshExtruder, tempThreshBed, log, lockPath):
	isPrinting = False
	
	r = requests.get(
		printerApiUrl,
		headers=headers
		)	
		
	Log(log, ('Check Printing status code (%s)' % r.status_code), LOG_LEVEL_TRACE)
	
	if(r.status_code == 200):
		forcePrinting = False
		try:
			jsonOut = r.json()
			printing = jsonOut['state']['flags']['printing']
			paused = jsonOut['state']['flags']['paused']
			extruderTarget = jsonOut['temperature']['tool0']['target']
			bedTarget = jsonOut['temperature']['bed']['target']
			extruderTemp = jsonOut['temperature']['tool0']['actual']
			bedTemp = jsonOut['temperature']['bed']['actual']
			Log(log, ('printing(%s) paused(%s) extruder(cur %s/tgt %s/thresh %s) bedTarget(cur %s/tgt %s/thresh %s)\n' % (printing,paused,extruderTemp,extruderTarget,tempThreshExtruder,bedTemp,bedTarget,tempThreshBed)), LOG_LEVEL_TRACE)			
		except:
			#assume we are printing if we can't read the print status to be safe
			Log(log,("Error reading print status - %s"), LOG_LEVEL_ERROR)
			forcePrinting = True

		#we write this to a file so if we crash or restart during a print, we will know we used to be printing, will require a manual reconnect in these cases
		f = open(lockPath,"w")
		doLock = False
		if(printing == True or paused == True or extruderTarget > 0 or bedTarget > 0 or forcePrinting == True):
			doLock = True
		else:
			if(tempDetectEnabled == 'true'):
				if(extruderTemp > tempThreshExtruder or bedTemp > tempThreshBed):
					doLock = True
		if(doLock == True):
			isPrinting = True
			f.write('printing')	
		f.close()
		#os.chmod(lockFile, stat.S_IREAD | stat.S_IWRITE | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
	else:
		Log(log, ('Failed to get print status (%d)\n' % r.status_code), LOG_LEVEL_ERROR)
	
	if(isPrinting):
		Log(log, ('Printing'), LOG_LEVEL_TRACE)
	else:
		Log(log, ('Not Printing'), LOG_LEVEL_TRACE)
	return isPrinting
#end CheckPrinting()

#Main()
LOCK_FILENAME = '.printlock'
lockPath = sys.path[0] + '/' + LOCK_FILENAME

LOG_FILENAME = 'oprintmon.log'
logPath = sys.path[0] + '/' + LOG_FILENAME

CONFIG_FILENAME = 'oprintmon.config'
configPath = sys.path[0] + '/' + CONFIG_FILENAME

log = open(logPath, "w+")

Log(log, ('oprintmon start'), LOG_LEVEL_ERROR)

if(os.path.isfile(configPath) == False):
	Log(log, ('Failed to load config (%s) - exiting' % configPath), LOG_LEVEL_ERROR)
	sys.exit(1)
	
configFile = open(configPath, "r")

try:
	configJson = json.load(configFile)
except:
	Log(log, ('Failed to read % - exiting' % configPath), LOG_LEVEL_ERROR)
	configFile.close()
	sys.exit(1)

octopiConfig = configJson['config']['octopi']
apiKey = octopiConfig['api-key']
baseUrl = octopiConfig['url-base']
port = octopiConfig['serial-port']
try:
	baudRate = int(octopiConfig['baud'])
except:
	Log(log, ('baudRate must be a number - exiting'), LOG_LEVEL_ERROR)
	sys.exit(1)

configConnect = configJson['config']['connect']
try:
	sleepTimeConnecting = float(configConnect['sleep-time'])
except:
	Log(log, ('sleep-time must be a number - exiting'), LOG_LEVEL_ERROR)	
	sys.exit(1)

printmonConfig = configJson['config']['printmon']
tempDetectEnabled = printmonConfig['detection-enabled'] == "true"
if(tempDetectEnabled) :
	try:
		tempThreshBed = int(printmonConfig['thresh-bed'])
		tempThreshExtruder = int(printmonConfig['thresh-hotend'])
	except:
		Log(log, ('temp thresholds must be numbers - exiting'), LOG_LEVEL_ERROR)	
		sys.exit(1)
try:
	sleepTimePrinting = float(printmonConfig['sleep-time'])
except:
	Log(log, ('sleep-time must be a number - exiting'), LOG_LEVEL_ERROR)	
	sys.exit(1)
	
Log(log, ('api-key (%s), base_url(%s), baud (%s), port (%s), sleeptime(connect) (%s), sleeptime(print) (%s) tempDetect(%s), bedThresh(%s), extruderThresh(%s)' % (apiKey, baseUrl, baudRate, port, sleepTimeConnecting, sleepTimePrinting, tempDetectEnabled, tempThreshBed, tempThreshExtruder)), LOG_LEVEL_TRACE)
	
headers = {'X-Api-Key': apiKey}
connectionApiURL = baseUrl + 'api/connection'
printerApiUrl = baseUrl + 'api/printer'

isPrinting = False

log.flush()
		
while(1):
	connectionState = 'Invalid'
	status_code = 0
	sleepTime = sleepTimeConnecting
	
	if(os.path.isfile(lockPath)):
		f = open(lockPath, "r")
		lockResult = f.read()
		isPrinting = lockResult == 'printing'
		Log(log, ('Read (%s) isPrinting = %s' % (lockResult, isPrinting)), LOG_LEVEL_TRACE)
		f.close()
		
	try:
		Log(log, ('%s\n%s' % (connectionApiURL, headers)), LOG_LEVEL_TRACE)
		r = requests.get(connectionApiURL, headers=headers)
	except:
		Log(log, ("failed to send request, server may not be up yet\n"), LOG_LEVEL_ERROR)
		status_code = -1
		
	if(status_code != -1):
		status_code = r.status_code
		
	#received valid connection info
	if(status_code == 200):
		jsonOut = r.json()
		#get connection state
		connectionState = jsonOut['current']['state']
		currentPort = jsonOut['current']['port']
		Log(log,('State = %s Port = %s' % (connectionState, currentPort)), LOG_LEVEL_TRACE)
						
		connecting = False			
		#if we are not connected and not printing, try to connect
		if(not isPrinting):	
			if(connectionState == 'Closed'):
				connecting = True
				json = {
					"command": "connect",
					"port": port,
					"baudrate": baudRate,
				}

				Log(log, ("Connecting to url %s on port %s with baud rate %s\n" % (connectionApiURL, port, baudRate)), LOG_LEVEL_TRACE)

				r = requests.post(
					connectionApiURL,
					json=json,
					headers=headers
				)	
	
				if(r.status_code == 204):
					Log(log, ("Connect Request Successful\n"), LOG_LEVEL_TRACE)
				else:	
					Log(log, ("Connect Request Failed (%d)\n" % r.status_code), LOG_LEVEL_ERROR)
		else:
			sleepTime = sleepTimePrinting
			
		if(not connecting):
			sleepTime = sleepTimePrinting
			if(connectionState != 'Closed' and connectionState != 'Connecting' and connectionState != 'Disconnecting'):
				isPrinting = CheckPrinting(printerApiUrl, headers, tempDetectEnabled, tempThreshExtruder, tempThreshBed, log, lockPath)
							
	Log(log, ('Sleeping for %s seconds' % sleepTime), LOG_LEVEL_TRACE)
	log.flush()
	time.sleep(sleepTime)
#end Main



