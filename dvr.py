import subprocess
import os
import time
import logging
from datetime import datetime
import signal
import sys
import threading

class DVRBackend:
    def __init__(self, rtsp_url, output_dir="dvr_recordings", chunk_duration=300, max_dvr_size_gb=4):
        self.rtsp_url = rtsp_url
        self.output_dir = output_dir
        self.chunk_duration = chunk_duration  # 5 minutes by default
        self.max_dvr_size_gb = max_dvr_size_gb
        self.is_recording = False
        self.current_process = None
        
        # Set up logging
        self.setup_logging()
        
    def setup_logging(self):
        """Set up the logging system."""
        os.makedirs('logs', exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('logs/dvr_backend.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('DVRBackend')
        
    def check_ffmpeg(self):
        """Check if FFmpeg is available."""
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            self.logger.info("✅ FFmpeg check successful.")
            return True
        except Exception as e:
            self.logger.error(f"❌ FFmpeg error: {e}")
            return False
    
    def get_current_timestamp(self):
        """Get a formatted timestamp."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def create_output_directory(self):
        """Create the output directory with the current date."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        full_output_dir = os.path.join(self.output_dir, date_str)
        os.makedirs(full_output_dir, exist_ok=True)
        return full_output_dir
    
    def start_recording(self):
        """Start the continuous recording process."""
        if not self.check_ffmpeg():
            return False
            
        self.is_recording = True
        self.logger.info("🚀 Starting DVR backend...")
        self.logger.info(f"📹 RTSP URL: {self.rtsp_url.split('@')[0]}******")

        # Start the garbage collector thread
        gc_thread = threading.Thread(target=self.garbage_collector_loop, daemon=True)
        gc_thread.start()
        self.logger.info("🗑️ Garbage collector started.")
        
        # Handle termination signals
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        restart_count = 0
        max_restarts = 10
        
        while self.is_recording and restart_count < max_restarts:
            try:
                output_dir = self.create_output_directory()
                
                self.logger.info(f"📁 Recording to directory: {output_dir}")
                
                cmd = [
                    'ffmpeg',
                    '-rtsp_transport', 'tcp',
                    '-timeout', '30000000',  # 30-second timeout for input
                    '-use_wallclock_as_timestamps', '1',
                    '-fflags', '+genpts',
                    '-i', self.rtsp_url,
                    '-c', 'copy',
                    '-f', 'segment',
                    '-segment_time', str(self.chunk_duration),
                    '-segment_format', 'mpegts',
                    '-reset_timestamps', '1',
                    '-avoid_negative_ts', 'make_zero',
                    '-strftime', '1',
                    os.path.join(output_dir, 'chunk_%Y-%m-%d_%H-%M-%S.ts')
                ]
                
                self.logger.info("🎥 Starting recording...")
                self.current_process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Monitor the process
                self.monitor_ffmpeg_process()
                
                restart_count += 1
                if self.is_recording:
                    wait_time = min(restart_count * 10, 60)  # Exponential backoff
                    self.logger.warning(f"🔄 Restarting recording in {wait_time} seconds...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                self.logger.error(f"💥 Critical error: {e}")
                time.sleep(30)
                
        if restart_count >= max_restarts:
            self.logger.error("🛑 Maximum restart limit reached. Stopping...")
    
    def monitor_ffmpeg_process(self):
        """Monitor the FFmpeg process and log events."""
        while self.current_process and self.current_process.poll() is None:
            try:
                # Read FFmpeg's output to detect new files
                output_bytes = self.current_process.stderr.readline()
                if not output_bytes:
                    break
                output = output_bytes.decode('utf-8', errors='ignore')

                if "Opening" in output and ".ts" in output:
                    self.logger.info(f"📄 New file created: {output.strip()}")
                elif "frame=" in output and "fps=" in output:
                    # Log progress roughly every 100 frames
                    if "frame=" in output:
                        print(f"📊 {datetime.now().strftime('%H:%M:%S')} - Recording...", end='\r')
                else:
                    self.logger.info(f"FFMPEG_STDERR: {output.strip()}")
            except Exception as e:
                # This can happen if the process is terminated while reading, which is normal on stop.
                if self.is_recording:
                    self.logger.error(f"Error reading ffmpeg output: {e}")
                break
                
        # Process has finished
        if self.current_process:
            return_code = self.current_process.poll()
            if return_code != 0 and self.is_recording:
                self.logger.warning(f"⚠️ FFmpeg terminated with code: {return_code}")

    def garbage_collector(self):
        """Deletes oldest recordings if DVR size exceeds the limit."""
        max_size_bytes = self.max_dvr_size_gb * 1024 * 1024 * 1024
        
        try:
            files = []
            current_size_bytes = 0
            for dirpath, _, filenames in os.walk(self.output_dir):
                for f in filenames:
                    if f.endswith('.ts'):
                        fp = os.path.join(dirpath, f)
                        try:
                            file_size = os.path.getsize(fp)
                            file_mtime = os.path.getmtime(fp)
                            current_size_bytes += file_size
                            files.append((file_mtime, file_size, fp))
                        except OSError:
                            continue # File might have been deleted between listing and stat

            self.logger.info(f"Garbage Collector: Current DVR size is {current_size_bytes / (1024**3):.2f} GB. Max size is {self.max_dvr_size_gb} GB.")

            if current_size_bytes > max_size_bytes:
                self.logger.warning("DVR size exceeds limit. Deleting oldest files...")
                # Sort files by modification time (oldest first)
                files.sort()
                
                while current_size_bytes > max_size_bytes and files:
                    mtime, size, path = files.pop(0) # Get oldest file
                    try:
                        self.logger.info(f"Deleting old file: {path} ({size / (1024**2):.2f} MB)")
                        os.remove(path)
                        current_size_bytes -= size
                    except OSError as e:
                        self.logger.error(f"Error deleting file {path}: {e}")

            # Clean up empty directories
            for dirpath, dirnames, filenames in os.walk(self.output_dir, topdown=False):
                if not dirnames and not filenames:
                    try:
                        os.rmdir(dirpath)
                        self.logger.info(f"Removed empty directory: {dirpath}")
                    except OSError as e:
                        self.logger.error(f"Error removing empty directory {dirpath}: {e}")
        except Exception as e:
            self.logger.error(f"Error in garbage collector: {e}")

    def garbage_collector_loop(self):
        """Runs the garbage collector periodically."""
        while self.is_recording:
            self.garbage_collector()
            # Wait for an hour
            time.sleep(3600)
    
    def signal_handler(self, signum, frame):
        """Handle termination signals."""
        self.logger.info(f"🛑 Signal {signum} received. Stopping recording...")
        self.stop_recording()
    
    def stop_recording(self):
        """Stop the recording gracefully."""
        self.is_recording = False
        if self.current_process and self.current_process.poll() is None:
            self.logger.info("⏹️ Sending stop signal to FFmpeg (q) to finalize file...")
            try:
                # Send 'q' to ffmpeg's stdin for a graceful shutdown
                self.current_process.stdin.write(b'q')
                self.current_process.stdin.close()
                # Wait for the process to terminate
                self.current_process.wait(timeout=15)
            except (IOError, ValueError, BrokenPipeError):
                # If stdin communication fails, force termination
                self.logger.warning("Could not communicate with FFmpeg. Forcing termination (last file may be corrupt).")
                self.current_process.terminate()
                try:
                    self.current_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.current_process.kill()
            except subprocess.TimeoutExpired:
                # If it doesn't respond, force termination
                self.logger.warning("FFmpeg did not respond to stop signal. Forcing termination (last file may be corrupt).")
                self.current_process.kill()
        
        self.logger.info("✅ DVR backend stopped correctly.")
    
    def get_status(self):
        """Get the DVR status."""
        return {
            "recording": self.is_recording,
            "output_dir": self.output_dir,
            "timestamp": self.get_current_timestamp(),
            "chunk_duration": self.chunk_duration
        }

# Configuration and execution
if __name__ == "__main__":
    # Check for necessary command-line arguments
    if len(sys.argv) < 4:
        print("Usage: python dvr.py <user> <password> <camera_ip>")
        sys.exit(1)

    # Configuration from command-line arguments
    USER = sys.argv[1]
    PASSWORD = sys.argv[2]
    CAMERA_IP = sys.argv[3]
    STREAM_PATH = ""  # Optional, e.g., "h264", "main", "sub", etc.
    
    # Build RTSP URL
    RTSP_URL = f"rtsp://{USER}:{PASSWORD}@{CAMERA_IP}:554/{STREAM_PATH}"
    
    # Create DVR instance
    dvr = DVRBackend(
        rtsp_url=RTSP_URL,
        output_dir="dvr_recordings",
        chunk_duration=300,  # 5 minutes per chunk
        max_dvr_size_gb=4    # Set max DVR size to 4 GB
    )
    
    # Start recording
    try:
        dvr.start_recording()
    except KeyboardInterrupt:
        dvr.stop_recording()
    except Exception as e:
        dvr.logger.error(f"💥 Unhandled error: {e}")
        dvr.stop_recording()