import subprocess
import os
import time
import logging
from datetime import datetime
import signal
import sys

class DVRBackend:
    def __init__(self, rtsp_url, output_dir="dvr_recordings", chunk_duration=300):
        self.rtsp_url = rtsp_url
        self.output_dir = output_dir
        self.chunk_duration = chunk_duration  # 5 minutos por defecto
        self.is_recording = False
        self.current_process = None
        
        # Configurar logging
        self.setup_logging()
        
    def setup_logging(self):
        """Configurar sistema de logging"""
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
        """Verificar si FFmpeg está disponible"""
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            self.logger.info("✅ FFmpeg verificado correctamente")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error con FFmpeg: {e}")
            return False
    
    def get_current_timestamp(self):
        """Obtener timestamp formateado"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def create_output_directory(self):
        """Crear directorio de salida con fecha"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        full_output_dir = os.path.join(self.output_dir, date_str)
        os.makedirs(full_output_dir, exist_ok=True)
        return full_output_dir
    
    def start_recording(self):
        """Iniciar grabación continua"""
        if not self.check_ffmpeg():
            return False
            
        self.is_recording = True
        self.logger.info("🚀 Iniciando backend DVR...")
        self.logger.info(f"📹 RTSP URL: {self.rtsp_url.split('@')[0]}******")
        
        # Manejar señal de terminación
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        restart_count = 0
        max_restarts = 10
        
        while self.is_recording and restart_count < max_restarts:
            try:
                output_dir = self.create_output_directory()
                timestamp = datetime.now().strftime("%H-%M-%S")
                
                self.logger.info(f"📁 Grabando en directorio: {output_dir}")
                
                cmd = [
                    'ffmpeg',
                    '-rtsp_transport', 'tcp',
                    '-use_wallclock_as_timestamps', '1',
                    '-i', self.rtsp_url,
                    '-c', 'copy',
                    '-f', 'segment',
                    '-segment_time', str(self.chunk_duration),
                    '-segment_format', 'mp4',
                    '-segment_format_options', 'movflags=+faststart',
                    '-reset_timestamps', '1',
                    '-avoid_negative_ts', 'make_zero',
                    '-strftime', '1',
                    os.path.join(output_dir, 'chunk_%Y-%m-%d_%H-%M-%S.mp4')
                ]
                
                self.logger.info("🎥 Iniciando grabación...")
                self.current_process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Monitorear el proceso
                self.monitor_ffmpeg_process()
                
                restart_count += 1
                if self.is_recording:
                    wait_time = min(restart_count * 10, 60)  # Backoff exponencial
                    self.logger.warning(f"🔄 Reiniciando grabación en {wait_time} segundos...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                self.logger.error(f"💥 Error crítico: {e}")
                time.sleep(30)
                
        if restart_count >= max_restarts:
            self.logger.error("🛑 Máximo número de reinicios alcanzado. Deteniendo...")
    
    def monitor_ffmpeg_process(self):
        """Monitorear el proceso de FFmpeg y loggear eventos"""
        while self.current_process and self.current_process.poll() is None:
            try:
                # Leer salida de FFmpeg para detectar nuevos archivos
                output_bytes = self.current_process.stderr.readline()
                if not output_bytes:
                    break
                output = output_bytes.decode('utf-8', errors='ignore')

                if "Opening" in output and ".mp4" in output:
                    self.logger.info(f"📄 Nuevo archivo creado: {output.strip()}")
                elif "frame=" in output and "fps=" in output:
                    # Log cada 100 frames aproximadamente
                    if "frame=" in output:
                        print(f"📊 {datetime.now().strftime('%H:%M:%S')} - Grabando...", end='\r')
                else:
                    self.logger.info(f"FFMPEG_STDERR: {output.strip()}")
            except Exception as e:
                # Este error puede ocurrir si el proceso termina mientras leemos, es normal al parar.
                if self.is_recording:
                    self.logger.error(f"Error reading ffmpeg output: {e}")
                break
                
        # Proceso terminado
        if self.current_process:
            return_code = self.current_process.poll()
            if return_code != 0 and self.is_recording:
                self.logger.warning(f"⚠️ FFmpeg terminó con código: {return_code}")
    
    def signal_handler(self, signum, frame):
        """Manejar señales de terminación"""
        self.logger.info(f"🛑 Señal {signum} recibida. Deteniendo grabación...")
        self.stop_recording()
    
    def stop_recording(self):
        """Detener grabación de forma controlada."""
        self.is_recording = False
        if self.current_process and self.current_process.poll() is None:
            self.logger.info("⏹️ Enviando señal de parada a FFmpeg (q) para finalizar el archivo correctamente...")
            try:
                # Enviamos b'q' (bytes) al stdin de ffmpeg para que termine de forma controlada
                self.current_process.stdin.write(b'q')
                self.current_process.stdin.close()
                # Esperamos a que el proceso termine
                self.current_process.wait(timeout=15)
            except (IOError, ValueError, BrokenPipeError):
                # Si falla la comunicación por stdin, forzamos la terminación
                self.logger.warning("No se pudo comunicar con FFmpeg. Forzando terminación (el último archivo podría estar corrupto).")
                self.current_process.terminate()
                try:
                    self.current_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.current_process.kill()
            except subprocess.TimeoutExpired:
                # Si no responde, forzamos la terminación
                self.logger.warning("FFmpeg no respondió a la señal de parada. Forzando terminación (el último archivo podría estar corrupto).")
                self.current_process.kill()
        
        self.logger.info("✅ Backend DVR detenido correctamente")
    
    def get_status(self):
        """Obtener estado del DVR"""
        return {
            "recording": self.is_recording,
            "output_dir": self.output_dir,
            "timestamp": self.get_current_timestamp(),
            "chunk_duration": self.chunk_duration
        }

# Configuración y ejecución
if __name__ == "__main__":
    # Configuración - MODIFICA ESTOS VALORES
    USUARIO = sys.argv[1]
    CONTRASEÑA = sys.argv[2]
    IP_CAMARA = sys.argv[3]
    RUTA_STREAM = ""  # Ej: "h264", "main", "sub", etc.
    
    # Construir URL RTSP
    RTSP_URL = f"rtsp://{USUARIO}:{CONTRASEÑA}@{IP_CAMARA}:554/{RUTA_STREAM}"
    
    # Crear instancia del DVR
    dvr = DVRBackend(
        rtsp_url=RTSP_URL,
        output_dir="dvr_recordings",
        chunk_duration=300  # 5 minutos por chunk
    )
    
    # Iniciar grabación
    try:
        dvr.start_recording()
    except KeyboardInterrupt:
        dvr.stop_recording()
    except Exception as e:
        dvr.logger.error(f"💥 Error no manejado: {e}")
        dvr.stop_recording()