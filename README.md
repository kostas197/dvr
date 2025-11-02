# EZVIZ Wi-Fi Camera Cloud Backup Script

This script provides a simple yet effective solution for creating a cloud backup of your EZVIZ Wi-Fi camera's footage. It's designed to ensure your video data is securely stored off-site, leveraging a VPN connection.

## Features

- **RTSP Stream Capture:** Captures the live video stream from your EZVIZ camera (RTSP option must be enabled in the EZVIZ app settings).
- **Local Storage & Segmentation:** Splits the continuous stream into smaller `.ts` files, which are then saved to your local machine.
- **Off-site Backup via VPN:** Designed to run on a local machine connected to your network via a VPN (e.g., WireGuard), enabling an off-site backup solution for enhanced data redundancy.
- **Automated Cleanup:** Includes a dedicated thread to automatically delete older video files, preventing unlimited storage consumption and managing disk space efficiently.

## How it Works

1.  **Enable RTSP:** Ensure the RTSP viewing option is activated within your EZVIZ camera's mobile application settings.
2.  **Stream Processing:** The script connects to the RTSP stream, continuously reads the video feed, and segments it into manageable `.ts` files.
3.  **Secure Storage:** These segmented files are stored on your local machine. If this machine is configured with a VPN connection to your home network, it effectively acts as an off-site cloud backup.
4.  **Maintenance:** A background process periodically prunes older files, maintaining a defined history length and optimizing storage usage.

This script was co-created with AI, and this human hypervisor
