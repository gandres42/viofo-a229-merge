import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
import av

def find_video_files(root_path, extensions=('.mp4', '.mov', '.avi', '.mkv')):
    video_files = []
    for dirpath, _, filenames in os.walk(root_path):
        for filename in filenames:
            if filename.lower().endswith(extensions):
                video_files.append(os.path.join(dirpath, filename))
    return video_files

def print_video_metadata(file_path):
    """Print all metadata for a video file using PyAV."""
    try:
        container = av.open(file_path)
        print(f"Metadata for {file_path}:")
        for key, value in container.metadata.items():
            print(f"  {key}: {value}")
        for stream in container.streams:
            print(f"Stream {stream.index} ({stream.type}):")
            for key, value in stream.metadata.items():
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"Error reading metadata from {file_path}: {e}")

def extract_date(file_path):
    """Extract creation date using PyAV (direct FFmpeg binding, much faster)"""
    container = av.open(file_path)
    creation_time = container.metadata.get('creation_time')
    if creation_time:
        # Parse datetime as naive (assume it's already in local time)
        # Remove timezone indicator and parse
        date_str = creation_time.replace('Z', '').split('+')[0].split('.')[0]
        date_obj = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
        return (file_path, date_obj, creation_time)
    return None

def cluster_videos(videos, max_gap_seconds=300):
    """
    Cluster videos into continuous segments based on time gaps.
    Videos separated by more than max_gap_seconds are considered separate segments.
    Default is 5 minutes to allow for brief stops.
    """
    if not videos:
        return []
    
    clusters = []
    current_cluster = [videos[0]]
    
    for i in range(1, len(videos)):
        time_gap = (videos[i][1] - videos[i-1][1]).total_seconds()
        
        # If gap is more than max_gap_seconds, start a new cluster
        if time_gap > max_gap_seconds:
            clusters.append(current_cluster)
            current_cluster = [videos[i]]
        else:
            current_cluster.append(videos[i])
    
    # Don't forget the last cluster
    clusters.append(current_cluster)
    return clusters

def merge_videos(cluster, output_file, camera_type):
    """
    Merge a cluster of videos into a single output file using ffmpeg concat demuxer.
    """
    # Create a temporary file list for ffmpeg concat
    concat_file = f"concat_list_{camera_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    try:
        with open(concat_file, 'w') as f:
            for video_path, _, _ in cluster:
                # Escape single quotes and write in ffmpeg concat format
                escaped_path = video_path.replace("'", "'\\''")
                f.write(f"file '{escaped_path}'\n")
        
        # Use ffmpeg to merge videos
        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", output_file]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if result.returncode == 0:
            return True
        else:
            print(f"Error merging videos into {output_file}")
            return False
    finally:
        # Clean up the temporary concat file
        if os.path.exists(concat_file):
            os.remove(concat_file)

def process_clusters(clusters, camera_type):
    """Process all clusters for a given camera (front or rear)."""
    output_dir = Path("Merged")
    output_dir.mkdir(exist_ok=True)
    
    for i, cluster in tqdm(enumerate(clusters), total=len(clusters), desc=f"Merging {camera_type} videos"):
        start_time = cluster[0][1]
        end_time = cluster[-1][1]
        
        # Format: start_end_camera.mp4
        start_str = start_time.strftime("%Y%m%d_%H%M%S")
        end_str = end_time.strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"{start_str}_{end_str}_{camera_type}.mp4"
        
        if merge_videos(cluster, str(output_file), f"{camera_type}_{i}"):
            # Set file modification and access times to match video start time
            # Convert naive datetime to timestamp (assumes local time)
            timestamp = start_time.timestamp()
            os.utime(str(output_file), (timestamp, timestamp))

# Find all video files
video_path = sys.argv[1] if len(sys.argv) > 1 else exit()
files = find_video_files(video_path)
print(f"Found {len(files)} video files in {video_path}.")

# Process files in parallel
front = []
rear = []
with ThreadPoolExecutor(max_workers=12) as executor:
    futures = {executor.submit(extract_date, file): file for file in files}
    for future in tqdm(as_completed(futures), total=len(files), desc="Sorting videos"):
        result = future.result()
        stem = Path(result[0]).stem
        if stem:
            if stem[-1].upper() == 'F':
                front.append(result)
            elif stem[-1].upper() == 'R':
                rear.append(result)

# Sort by date
front.sort(key=lambda x: x[1])
rear.sort(key=lambda x: x[1])

# Cluster and merge
front_clusters = cluster_videos(front)
process_clusters(front_clusters, "front")

rear_clusters = cluster_videos(rear)
process_clusters(rear_clusters, "rear")

