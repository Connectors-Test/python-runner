import uuid
import json
import os
import subprocess
import sys
import time
from threading import Thread

from flask import Flask, Response, jsonify, render_template, request
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError

# --- Application Setup ---
app = Flask(__name__, template_folder='.')
JOBS_FILE = "cron_jobs.json"
LOGS_DIR = "logs"

# --- Scheduler Setup ---
# Using BackgroundScheduler to run tasks in the background.
scheduler = BackgroundScheduler()
scheduler.start()

# --- Job Persistence ---
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

def load_jobs_from_file():
    """Loads job definitions from the JSON file."""
    if not os.path.exists(JOBS_FILE):
        return {}
    try:
        with open(JOBS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

def save_jobs_to_file(jobs):
    """Saves job definitions to the JSON file."""
    with open(JOBS_FILE, 'w') as f:
        json.dump(jobs, f, indent=4)

# --- Job Execution Logic ---
def run_script(script_content, job_id):
    """
    Executes a given Python script string in a subprocess and logs its output.
    """
    start_time = time.strftime('%Y-%m-%d %H:%M:%S %Z')
    print(f"--- Running job {job_id} at {start_time} ---")
    log_file_path = os.path.join(LOGS_DIR, f"{job_id}.log")

    try:
        # We pass the script content via stdin to a python interpreter process
        process = subprocess.Popen(
            [sys.executable, '-c', script_content],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        stdout, stderr = process.communicate()

        if stdout:
            print(f"Output from {job_id}:\n{stdout.strip()}")
        if stderr:
            print(f"Error from {job_id}:\n{stderr.strip()}", file=sys.stderr)
        
        log_entry = {
            "timestamp": start_time,
            "stdout": stdout.strip(),
            "stderr": stderr.strip()
        }
        with open(log_file_path, 'a') as log_file:
            log_file.write(json.dumps(log_entry) + '\n')

        print(f"--- Finished job {job_id} ---")

    except Exception as e:
        error_log = {"timestamp": start_time, "stdout": "", "stderr": f"Failed to execute script: {e}"}
        with open(log_file_path, 'a') as log_file:
            log_file.write(json.dumps(error_log) + '\n')
        print(f"Failed to execute script for job {job_id}: {e}", file=sys.stderr)


def add_or_update_scheduler_job(job_id, script, interval):
    """Adds a new job or updates an existing one in APScheduler."""
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass  # Job doesn't exist, which is fine.
    
    scheduler.add_job(
        run_script,
        'interval',
        seconds=interval,
        id=job_id,
        args=[script, job_id],
        replace_existing=True
    )
    print(f"Scheduled job '{job_id}' to run every {interval} seconds.")


def remove_scheduler_job(job_id):
    """Removes a job from the scheduler."""
    try:
        scheduler.remove_job(job_id)
        print(f"Unscheduled job '{job_id}'.")
    except JobLookupError:
        print(f"Could not find job '{job_id}' in scheduler to remove.")


# --- Flask API Routes ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    """Handles browser's request for a favicon."""
    return '', 204

@app.route('/test-script')
def test_script():
    """
    Runs a script for testing and streams its output using Server-Sent Events.
    """
    script = request.args.get('script', '')

    def generate_logs():
        process = None
        try:
            # Start the script as a subprocess
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", script], # -u for unbuffered output
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Redirect stderr to stdout
                text=True,
                encoding='utf-8'
            )

            # Stream output line by line
            for line in iter(process.stdout.readline, ''):
                stream_type = 'stdout'
                # A simple heuristic to detect errors
                if 'error' in line.lower() or 'traceback' in line.lower():
                    stream_type = 'stderr'
                
                data = json.dumps({'line': line.strip(), 'stream': stream_type})
                yield f"data: {data}\n\n"
                time.sleep(0.01) # Small delay to allow client to process

        except Exception as e:
            data = json.dumps({'line': f"Error starting script: {e}", 'stream': 'stderr'})
            yield f"data: {data}\n\n"
        finally:
            if process:
                process.stdout.close()
                process.wait() # Wait for the process to terminate

    return Response(generate_logs(), mimetype='text/event-stream')

@app.route('/jobs/<job_id>/logs', methods=['GET'])
def get_job_logs(job_id):
    """Returns all log entries for a given job."""
    log_file_path = os.path.join(LOGS_DIR, f"{job_id}.log")
    if not os.path.exists(log_file_path):
        return jsonify([])

    logs = []
    try:
        with open(log_file_path, 'r') as log_file:
            for line in log_file:
                if line.strip():
                    logs.append(json.loads(line))
        # Return newest logs first
        return jsonify(sorted(logs, key=lambda x: x['timestamp'], reverse=True))
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error reading log file for {job_id}: {e}", file=sys.stderr)
        return jsonify({"error": "Could not read log file"}), 500


@app.route('/jobs', methods=['GET'])
def get_jobs():
    """Returns a list of all saved jobs."""
    jobs = load_jobs_from_file()
    return jsonify(list(jobs.values()))


@app.route('/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    """Returns a single job by its ID."""
    jobs = load_jobs_from_file()
    job = jobs.get(job_id)
    if job:
        return jsonify(job)
    return jsonify({'error': 'Job not found'}), 404

@app.route('/jobs', methods=['POST'])
def create_job():
    """Creates a new job, saves it, and schedules it."""
    data = request.json
    if not data or 'name' not in data or 'script' not in data or 'interval' not in data:
        return jsonify({'error': 'Missing name, script, or interval'}), 400

    jobs = load_jobs_from_file()
    job_id = str(uuid.uuid4())
    new_job = {
        'name': data['name'],
        'id': job_id,
        'script': data['script'],
        'interval': int(data['interval'])
    }
    jobs[job_id] = new_job
    save_jobs_to_file(jobs)
    add_or_update_scheduler_job(job_id, new_job['script'], new_job['interval'])
    return jsonify(new_job), 201

@app.route('/jobs/<job_id>', methods=['PUT'])
def update_job(job_id):
    """Updates an existing job."""
    jobs = load_jobs_from_file()
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404

    data = request.json
    if not data or 'name' not in data or 'script' not in data or 'interval' not in data:
        return jsonify({'error': 'Missing name, script, or interval'}), 400

    updated_job = {
        'name': data['name'],
        'id': job_id,
        'script': data['script'],
        'interval': int(data['interval'])
    }
    jobs[job_id] = updated_job
    save_jobs_to_file(jobs)
    add_or_update_scheduler_job(job_id, updated_job['script'], updated_job['interval'])
    return jsonify(updated_job)

@app.route('/jobs/<job_id>', methods=['DELETE'])
def delete_job(job_id):
    """Deletes a job."""
    jobs = load_jobs_from_file()
    if job_id in jobs:
        del jobs[job_id]
        save_jobs_to_file(jobs)
        remove_scheduler_job(job_id)
        # Also delete the log file
        log_file_path = os.path.join(LOGS_DIR, f"{job_id}.log")
        if os.path.exists(log_file_path):
            os.remove(log_file_path)
        return jsonify({'message': 'Job deleted'})
    return jsonify({'error': 'Job not found'}), 404

if __name__ == '__main__':
    # Load existing jobs and schedule them on startup
    print("Loading and scheduling existing jobs...")
    all_jobs = load_jobs_from_file()
    for job_id, job_details in all_jobs.items():
        add_or_update_scheduler_job(job_id, job_details['script'], job_details['interval'])

    # Render expects 0.0.0.0 + dynamic port
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask server at http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)
