# app.py - Main Flask Application with Complete CORS Fix
from flask import Flask, render_template, request, jsonify, make_response
from flask_cors import CORS, cross_origin
import subprocess
import threading
import queue
import time
import sys
import os
import uuid
import traceback
from datetime import datetime
import signal
import json

app = Flask(__name__)

# Complete CORS configuration
app.config['CORS_HEADERS'] = 'Content-Type'
app.config['CORS_ORIGINS'] = '*'
app.config['CORS_METHODS'] = ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']
app.config['CORS_ALLOW_HEADERS'] = ['Content-Type', 'Authorization', 'Access-Control-Allow-Origin']

# Enable CORS with comprehensive settings
CORS(app, 
     origins='*',
     allow_headers=['Content-Type', 'Authorization', 'Access-Control-Allow-Origin'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
     supports_credentials=False,
     max_age=3600)

# Global storage for script execution
executions = {}
execution_threads = {}
scheduled_tasks = {}

class ScriptExecutor:
    def __init__(self, execution_id, script_code, execution_type='manual'):
        self.execution_id = execution_id
        self.script_code = script_code
        self.execution_type = execution_type
        self.process = None
        self.output_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.status = 'pending'
        self.start_time = datetime.now()
        self.end_time = None
        self.is_paused = False
        self.is_stopped = False
        self.output_history = []
        self.error_history = []
        
    def run(self):
        """Execute the Python script in a subprocess"""
        try:
            self.status = 'running'
            
            # Create a temporary Python file
            temp_file = f'temp_script_{self.execution_id}.py'
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(self.script_code)
            
            # Run the script in a subprocess
            self.process = subprocess.Popen(
                [sys.executable, temp_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Create threads to read output and errors
            output_thread = threading.Thread(target=self._read_output)
            error_thread = threading.Thread(target=self._read_errors)
            
            output_thread.start()
            error_thread.start()
            
            # Wait for process to complete
            self.process.wait()
            
            output_thread.join()
            error_thread.join()
            
            # Clean up temp file
            try:
                os.remove(temp_file)
            except:
                pass
                
            if not self.is_stopped:
                self.status = 'completed'
            self.end_time = datetime.now()
            
        except Exception as e:
            self.status = 'error'
            self.error_history.append(str(e))
            self.error_queue.put(traceback.format_exc())
            
    def _read_output(self):
        """Read stdout from the subprocess"""
        try:
            for line in iter(self.process.stdout.readline, ''):
                if line:
                    self.output_history.append(line.strip())
                    self.output_queue.put(line.strip())
                    
                while self.is_paused:
                    time.sleep(0.1)
                    
                if self.is_stopped:
                    break
        except:
            pass
            
    def _read_errors(self):
        """Read stderr from the subprocess"""
        try:
            for line in iter(self.process.stderr.readline, ''):
                if line:
                    self.error_history.append(line.strip())
                    self.error_queue.put(line.strip())
                    
                if self.is_stopped:
                    break
        except:
            pass
            
    def pause(self):
        """Pause the execution"""
        self.is_paused = True
        self.status = 'paused'
        
    def resume(self):
        """Resume the execution"""
        self.is_paused = False
        self.status = 'running'
        
    def stop(self):
        """Stop the execution"""
        self.is_stopped = True
        self.status = 'stopped'
        if self.process:
            try:
                self.process.terminate()
                time.sleep(0.5)
                if self.process.poll() is None:
                    self.process.kill()
            except:
                pass
                
    def get_output(self):
        """Get all output from the queue"""
        output = []
        while not self.output_queue.empty():
            try:
                output.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return output
        
    def get_errors(self):
        """Get all errors from the queue"""
        errors = []
        while not self.error_queue.empty():
            try:
                errors.append(self.error_queue.get_nowait())
            except queue.Empty:
                break
        return errors

class ScheduledExecutor:
    def __init__(self, script_code, interval=2):
        self.script_code = script_code
        self.interval = interval
        self.is_running = False
        self.thread = None
        self.execution_count = 0
        
    def start(self):
        """Start scheduled execution"""
        self.is_running = True
        self.thread = threading.Thread(target=self._run_scheduled)
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        """Stop scheduled execution"""
        self.is_running = False
        
    def _run_scheduled(self):
        """Run the script at scheduled intervals"""
        while self.is_running:
            execution_id = str(uuid.uuid4())
            executor = ScriptExecutor(execution_id, self.script_code, 'scheduled')
            executions[execution_id] = executor
            
            thread = threading.Thread(target=executor.run)
            thread.daemon = True
            thread.start()
            execution_threads[execution_id] = thread
            
            self.execution_count += 1
            time.sleep(self.interval)

# Helper function to create CORS response
def cors_response(data, status=200):
    response = make_response(jsonify(data), status)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

@app.route('/')
def index():
    """Serve the main dashboard"""
    return render_template('index.html')

@app.route('/api/run', methods=['POST', 'OPTIONS'])
def run_script():
    """Run a Python script"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        data = request.json
        script_code = data.get('script', '')
        
        if not script_code:
            return cors_response({'error': 'No script provided'}, 400)
            
        execution_id = str(uuid.uuid4())
        executor = ScriptExecutor(execution_id, script_code)
        executions[execution_id] = executor
        
        # Run in a separate thread
        thread = threading.Thread(target=executor.run)
        thread.daemon = True
        thread.start()
        execution_threads[execution_id] = thread
        
        return cors_response({
            'execution_id': execution_id,
            'status': 'started',
            'timestamp': executor.start_time.isoformat()
        })
        
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/schedule', methods=['POST', 'OPTIONS'])
def schedule_script():
    """Schedule a script to run every 2 seconds"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        data = request.json
        script_code = data.get('script', '')
        schedule_id = data.get('schedule_id', 'default')
        
        if not script_code:
            return cors_response({'error': 'No script provided'}, 400)
            
        # Stop existing schedule if any
        if schedule_id in scheduled_tasks:
            scheduled_tasks[schedule_id].stop()
            
        # Create new scheduled executor
        scheduler = ScheduledExecutor(script_code, interval=2)
        scheduled_tasks[schedule_id] = scheduler
        scheduler.start()
        
        return cors_response({
            'schedule_id': schedule_id,
            'status': 'scheduled',
            'interval': 2
        })
        
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/stop/<execution_id>', methods=['POST', 'OPTIONS'])
def stop_script(execution_id):
    """Stop a running script"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        if execution_id in executions:
            executions[execution_id].stop()
            return cors_response({'status': 'stopped'})
        return cors_response({'error': 'Execution not found'}, 404)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/pause/<execution_id>', methods=['POST', 'OPTIONS'])
def pause_script(execution_id):
    """Pause a running script"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        if execution_id in executions:
            executions[execution_id].pause()
            return cors_response({'status': 'paused'})
        return cors_response({'error': 'Execution not found'}, 404)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/resume/<execution_id>', methods=['POST', 'OPTIONS'])
def resume_script(execution_id):
    """Resume a paused script"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        if execution_id in executions:
            executions[execution_id].resume()
            return cors_response({'status': 'resumed'})
        return cors_response({'error': 'Execution not found'}, 404)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/stop_schedule/<schedule_id>', methods=['POST', 'OPTIONS'])
def stop_schedule(schedule_id):
    """Stop a scheduled task"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        if schedule_id in scheduled_tasks:
            scheduled_tasks[schedule_id].stop()
            del scheduled_tasks[schedule_id]
            return cors_response({'status': 'stopped'})
        return cors_response({'error': 'Schedule not found'}, 404)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/status/<execution_id>', methods=['GET', 'OPTIONS'])
def get_status(execution_id):
    """Get the status of an execution"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        if execution_id in executions:
            executor = executions[execution_id]
            return cors_response({
                'execution_id': execution_id,
                'status': executor.status,
                'output': executor.get_output(),
                'errors': executor.get_errors(),
                'start_time': executor.start_time.isoformat(),
                'end_time': executor.end_time.isoformat() if executor.end_time else None
            })
        return cors_response({'error': 'Execution not found'}, 404)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/executions', methods=['GET', 'OPTIONS'])
def get_executions():
    """Get list of all executions"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        exec_list = []
        for exec_id, executor in executions.items():
            exec_list.append({
                'execution_id': exec_id,
                'status': executor.status,
                'type': executor.execution_type,
                'start_time': executor.start_time.isoformat(),
                'end_time': executor.end_time.isoformat() if executor.end_time else None
            })
        return cors_response(exec_list)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

@app.route('/api/output/<execution_id>', methods=['GET', 'OPTIONS'])
def get_output(execution_id):
    """Get the output of a specific execution"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
        
    try:
        if execution_id in executions:
            executor = executions[execution_id]
            return cors_response({
                'output': executor.output_history,
                'errors': executor.error_history
            })
        return cors_response({'error': 'Execution not found'}, 404)
    except Exception as e:
        return cors_response({'error': str(e)}, 500)

# Global CORS handler for all other routes
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        response = make_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Access-Control-Allow-Origin'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response

@app.after_request
def after_request(response):
    """Ensure CORS headers are set on all responses"""
    origin = request.headers.get('Origin', '*')
    response.headers['Access-Control-Allow-Origin'] = origin if origin else '*'
    response.headers['Access-Control-Allow-Credentials'] = 'false'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Max-Age'] = '3600'
    return response

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    print("=" * 50)
    print("Python Script Runner Dashboard")
    print("=" * 50)
    print("Server starting at http://localhost:5004")
    print("CORS is fully enabled for all origins")
    print("=" * 50)
    
    # Run with host='0.0.0.0' to allow external connections
    app.run(debug=True, threaded=True, host='0.0.0.0', port=5004)
