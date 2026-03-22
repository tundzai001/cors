-- Delete all test devices with LTPI prefix
-- Run this in pgAdmin on the devices database

-- Step 1: Delete RTCM sessions for these devices
DELETE FROM rtcm_stream_sessions 
WHERE serial LIKE 'LTPI%';

-- Step 2: Delete the devices themselves
DELETE FROM devices 
WHERE serial LIKE 'LTPI%';

-- Verify deletion (should return 0)
SELECT COUNT(*) as remaining_ltpi_devices 
FROM devices 
WHERE serial LIKE 'LTPI%';
