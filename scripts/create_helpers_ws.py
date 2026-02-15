#!/usr/bin/env python3
"""
Create input_text helpers via Home Assistant WebSocket API.
Uses built-in Python websocket-client library.
"""
import json
import os
import sys
import time
import ssl

try:
    from websocket import create_connection
except ImportError:
    print("ERROR: websocket-client not installed")
    print("Installing websocket-client...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "websocket-client"])
    from websocket import create_connection

def create_helpers():
    """Connect to HA WebSocket and create helpers."""
    
    # Get credentials
    ha_url = os.environ.get('HOME_ASSISTANT_URL', 'http://192.168.4.124:8123')
    ha_token = os.environ.get('HOME_ASSISTANT_TOKEN')
    
    if not ha_token:
        print("ERROR: HOME_ASSISTANT_TOKEN not set")
        sys.exit(1)
    
    # Convert http to ws
    ws_url = ha_url.replace('http://', 'ws://').replace('https://', 'wss://') + '/api/websocket'
    
    print(f"Connecting to {ws_url}...")
    
    # Connect
    ws = create_connection(ws_url, timeout=10)
    
    try:
        # Step 1: Receive auth_required
        msg = ws.recv()
        data = json.loads(msg)
        print(f"1. Received: {data['type']}")
        
        # Step 2: Send auth
        ws.send(json.dumps({
            "type": "auth",
            "access_token": ha_token
        }))
        
        msg = ws.recv()
        auth_result = json.loads(msg)
        print(f"2. Auth result: {auth_result['type']}")
        
        if auth_result['type'] != 'auth_ok':
            print(f"ERROR: Authentication failed")
            sys.exit(1)
        
        msg_id = 1
        
        # Step 3: Create helper 1
        print(f"\n3. Creating helper: p1s_slot_to_spool_binding_json")
        ws.send(json.dumps({
            "id": msg_id,
            "type": "input_text/create",
            "name": "P1S Slot to Spool Binding (JSON)",
            "initial": "{}",
            "min": 0,
            "max": 1024,
            "mode": "text"
        }))
        
        msg = ws.recv()
        response = json.loads(msg)
        print(f"   Response: {response}")
        
        if response.get('success'):
            print(f"   ✅ Created: {response.get('result', {}).get('id')}")
        else:
            error = response.get('error', {})
            print(f"   ❌ Failed: {error.get('code')} - {error.get('message')}")
            
            # If command not found, report and exit
            if error.get('code') == 'unknown_command':
                print(f"\n⚠️  WebSocket command 'input_text/create' not supported")
                print(f"Available methods:")
                print(f"  1. Manual UI creation (Settings → Helpers)")
                print(f"  2. YAML configuration (configuration.yaml)")
                return False
        
        msg_id += 1
        
        # Step 4: Create helper 2
        print(f"\n4. Creating helper: p1s_last_mapping_json")
        ws.send(json.dumps({
            "id": msg_id,
            "type": "input_text/create",
            "name": "P1S Last Mapping Result",
            "initial": "",
            "min": 0,
            "max": 2048,
            "mode": "text"
        }))
        
        msg = ws.recv()
        response = json.loads(msg)
        print(f"   Response: {response}")
        
        if response.get('success'):
            print(f"   ✅ Created: {response.get('result', {}).get('id')}")
        else:
            print(f"   ❌ Failed: {response.get('error')}")
        
        return True
        
    finally:
        ws.close()

if __name__ == '__main__':
    success = create_helpers()
    sys.exit(0 if success else 1)
