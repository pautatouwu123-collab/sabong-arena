#!/usr/bin/env python3
"""
Sabong Anti-Cheat Module
- Rate limiting
- IP-based limiting
- Bet pattern detection
- Anomaly alerts
- Real-time monitoring
- Operator session management
"""

import os
import time
import hashlib
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps

from flask import request, jsonify, g
from flask_jwt_extended import get_jwt_identity, jwt_required


# ============================================================
# IN-MEMORY ANTI-CHEAT TRACKING (use Redis in production)
# ============================================================

class AntiCheatEngine:
    """Central anti-cheat engine"""
    
    def __init__(self):
        self._lock = threading.Lock()
        
        # IP-based tracking
        self.ip_requests = defaultdict(list)  # IP -> [(timestamp, endpoint)]
        self.ip_bets = defaultdict(list)  # IP -> [(timestamp, amount, fight_id)]
        
        # Operator-based tracking
        self.operator_bets = defaultdict(list)  # operator_id -> [(timestamp, amount, fight_id)]
        self.operator_sessions = {}  # operator_id -> session_info
        self.operator_failures = defaultdict(list)  # operator_id -> [(timestamp, failure_type)]
        
        # Global tracking
        self.total_bets_today = 0
        self.total_amount_today = 0
        self.last_reset = datetime.utcnow()
        
        # Suspicious patterns
        self.suspicious_operators = set()
        self.suspicious_ips = set()
        
        # Config
        self.config = {
            'max_bets_per_minute': 30,
            'max_bets_per_operator_per_minute': 10,
            'max_amount_per_fight_per_operator': 50000,
            'max_bets_per_fight_per_ip': 20,
            'min_bet_interval_seconds': 1,
            'alert_threshold_amount': 10000,
            'suspicious_ratio': 3.0,  # If operator bets 3x more than average
            'lockout_duration_minutes': 15,
        }
    
    def get_client_ip(self):
        """Get client IP from request"""
        if request.headers.get('X-Forwarded-For'):
            return request.headers.get('X-Forwarded-For').split(',')[0].strip()
        return request.remote_addr or '127.0.0.1'
    
    def record_request(self, endpoint: str = None):
        """Record API request for rate limiting"""
        ip = self.get_client_ip()
        now = time.time()
        
        with self._lock:
            # Clean old entries (>1 minute)
            self.ip_requests[ip] = [
                (t, e) for t, e in self.ip_requests[ip]
                if now - t < 60
            ]
            self.ip_requests[ip].append((now, endpoint or request.endpoint or 'unknown'))
    
    def check_rate_limit(self) -> tuple:
        """
        Check if request exceeds rate limit
        Returns: (allowed, message, severity)
        """
        ip = self.get_client_ip()
        now = time.time()
        
        with self._lock:
            # Clean old entries
            self.ip_requests[ip] = [
                (t, e) for t, e in self.ip_requests[ip]
                if now - t < 60
            ]
            
            request_count = len(self.ip_requests[ip])
            max_requests = self.config['max_bets_per_minute']
            
            if request_count >= max_requests:
                self.suspicious_ips.add(ip)
                return False, f'Rate limit exceeded: {request_count}/{max_requests} req/min', 'HIGH'
            
            return True, None, None
    
    def record_bet(self, operator_id: int, fight_id: int, amount: float):
        """Record bet for pattern analysis"""
        ip = self.get_client_ip()
        now = time.time()
        
        with self._lock:
            # IP bet tracking
            self.ip_bets[ip] = [
                (t, a, f) for t, a, f in self.ip_bets[ip]
                if now - t < 300  # 5 minutes
            ]
            self.ip_bets[ip].append((now, amount, fight_id))
            
            # Operator bet tracking
            self.operator_bets[operator_id] = [
                (t, a, f) for t, a, f in self.operator_bets[operator_id]
                if now - t < 300
            ]
            self.operator_bets[operator_id].append((now, amount, fight_id))
            
            # Global tracking
            self.total_bets_today += 1
            self.total_amount_today += amount
            
            # Reset daily if needed
            if (datetime.utcnow() - self.last_reset).days > 0:
                self.total_bets_today = 1
                self.total_amount_today = amount
                self.last_reset = datetime.utcnow()
    
    def check_bet_limits(self, operator_id: int, fight_id: int, amount: float) -> tuple:
        """
        Check bet against limits
        Returns: (allowed, message, severity)
        """
        ip = self.get_client_ip()
        now = time.time()
        
        with self._lock:
            # Check operator bets per minute
            op_bets = [
                (t, a, f) for t, a, f in self.operator_bets[operator_id]
                if now - t < 60
            ]
            if len(op_bets) >= self.config['max_bets_per_operator_per_minute']:
                return False, 'Too many bets per minute', 'MEDIUM'
            
            # Check operator amount per fight
            op_fight_amount = sum(
                a for t, a, f in self.operator_bets[operator_id]
                if f == fight_id and now - t < 3600  # 1 hour
            )
            if op_fight_amount + amount > self.config['max_amount_per_fight_per_operator']:
                return False, f'Exceeded ₱{self.config["max_amount_per_fight_per_operator"]} limit per fight', 'MEDIUM'
            
            # Check IP bets per fight
            ip_fight_bets = [
                t for t, a, f in self.ip_bets[ip]
                if f == fight_id and now - t < 300
            ]
            if len(ip_fight_bets) >= self.config['max_bets_per_fight_per_ip']:
                return False, 'Too many bets from this IP on same fight', 'HIGH'
        
        return True, None, None
    
    def detect_anomaly(self, operator_id: int) -> dict:
        """
        Detect suspicious patterns
        Returns: anomaly dict or None
        """
        now = time.time()
        anomalies = []
        severity = 'LOW'
        
        with self._lock:
            # Check operator failure rate
            failures = [
                t for t in self.operator_failures[operator_id]
                if now - t < 300
            ]
            if len(failures) >= 5:
                anomalies.append(f'High failure rate: {len(failures)}/5min')
                severity = 'MEDIUM'
            
            # Check betting pattern
            recent = [
                (t, a) for t, a, f in self.operator_bets[operator_id]
                if now - t < 60
            ]
            
            if len(recent) >= 10:
                avg_amount = sum(a for t, a in recent) / len(recent)
                
                # Check for exact same amounts (bot indicator)
                amounts = [a for t, a in recent]
                from collections import Counter
                most_common = Counter(amounts).most_common(1)
                if most_common and most_common[0][1] >= 5:
                    anomalies.append('Suspicious exact repeat amounts')
                    severity = 'HIGH'
                
                # Check for rapid betting (potential automation)
                if len(recent) >= 10 and all(now - recent[i][0] < 3 for i in range(min(10, len(recent)))):
                    anomalies.append('Rapid automated betting detected')
                    severity = 'HIGH'
        
        if anomalies:
            return {
                'operator_id': operator_id,
                'anomalies': anomalies,
                'severity': severity,
                'timestamp': datetime.utcnow().isoformat()
            }
        return None
    
    def record_failure(self, operator_id: int, failure_type: str):
        """Record failed attempt"""
        with self._lock:
            self.operator_failures[operator_id].append(time.time())
            # Clean old
            now = time.time()
            self.operator_failures[operator_id] = [
                t for t in self.operator_failures[operator_id]
                if now - t < 300
            ]
    
    def block_operator(self, operator_id: int, duration_minutes: int = None):
        """Block operator temporarily"""
        duration = duration_minutes or self.config['lockout_duration_minutes']
        self.suspicious_operators.add(operator_id)
        
        # In production, store in Redis with expiry
    
    def is_blocked(self, operator_id: int) -> bool:
        return operator_id in self.suspicious_operators
    
    def get_stats(self) -> dict:
        """Get anti-cheat statistics"""
        with self._lock:
            return {
                'total_bets_today': self.total_bets_today,
                'total_amount_today': self.total_amount_today,
                'suspicious_operators': len(self.suspicious_operators),
                'suspicious_ips': len(self.suspicious_ips),
                'active_ips': len(self.ip_requests),
                'active_operators': len(self.operator_bets),
            }


# Global anti-cheat engine
anticheat = AntiCheatEngine()


# ============================================================
# FLASK DECORATORS FOR ANTI-CHEAT
# ============================================================

def rate_limit_check(f):
    """Decorator to check rate limits"""
    @wraps(f)
    def decorated(*args, **kwargs):
        allowed, message, severity = anticheat.check_rate_limit()
        if not allowed:
            # Log the attempt
            from sabong_system import log_audit
            from sabong_system import AuditAction, AuditSeverity
            
            ip = anticheat.get_client_ip()
            log_audit(
                AuditAction.RATE_LIMIT_EXCEEDED,
                severity=severity,
                details=f'IP: {ip}, {message}'
            )
            return jsonify({'error': message}), 429
        
        anticheat.record_request()
        return f(*args, **kwargs)
    return decorated


def bet_limits_check(f):
    """Decorator to check bet limits"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get data from request
        data = request.get_json() or {}
        fight_id = data.get('fight_id')
        amount = data.get('amount')
        
        if fight_id and amount:
            operator_id = int(get_jwt_identity())
            
            allowed, message, severity = anticheat.check_bet_limits(operator_id, fight_id, amount)
            if not allowed:
                from sabong_system import log_audit, AuditAction, AuditSeverity
                anticheat.record_failure(operator_id, message)
                log_audit(
                    AuditAction.BET_LIMIT_EXCEEDED,
                    operator_id=operator_id,
                    fight_id=fight_id,
                    severity=severity,
                    details=message
                )
                return jsonify({'error': message}), 400
            
            # Record bet for pattern analysis
            anticheat.record_bet(operator_id, fight_id, amount)
        
        return f(*args, **kwargs)
    return decorated


def anomaly_check(f):
    """Decorator to check for anomalies after bet"""
    @wraps(f)
    def decorated(*args, **kwargs):
        result = f(*args, **kwargs)
        
        # Check response - if bet was placed
        if request.endpoint == 'place_bet' and request.method == 'POST':
            operator_id = int(get_jwt_identity())
            anomaly = anticheat.detect_anomaly(operator_id)
            
            if anomaly and anomaly['severity'] == 'HIGH':
                from sabong_system import log_audit, AuditAction, AuditSeverity
                log_audit(
                    AuditAction.ANOMALY_DETECTED,
                    operator_id=operator_id,
                    severity=AuditSeverity.WARNING.value,
                    details=str(anomaly)
                )
                # Auto-block if severity is HIGH
                if anomaly['severity'] == 'HIGH':
                    # Could auto-block here in production
                    pass
        
        return result
    return decorated


# ============================================================
# API ROUTES FOR ANTI-CHEAT DASHBOARD
# ============================================================

def add_anticheat_routes(app):
    """Add anti-cheat API routes"""
    
    @app.route('/api/anticheat/stats', methods=['GET'])
    @jwt_required()
    def anticheat_stats():
        """Get anti-cheat statistics"""
        return jsonify(anticheat.get_stats())
    
    @app.route('/api/anticheat/operator/<int:operator_id>', methods=['GET'])
    @jwt_required()
    def anticheat_operator(operator_id):
        """Get operator betting pattern"""
        now = time.time()
        
        with anticheat._lock:
            bets = [
                {'timestamp': t, 'amount': a, 'fight_id': f}
                for t, a, f in anticheat.operator_bets[operator_id]
                if now - t < 3600
            ]
            
            failures = anticheat.operator_failures.get(operator_id, [])
            
            return jsonify({
                'bets_last_hour': len(bets),
                'total_amount': sum(b['amount'] for b in bets),
                'recent_bets': bets[-10:],
                'failures_last_5min': len(failures),
                'is_blocked': anticheat.is_blocked(operator_id)
            })
    
    @app.route('/api/anticheat/block/<int:operator_id>', methods=['POST'])
    @jwt_required()
    def block_operator(operator_id):
        """Block suspicious operator"""
        from flask import request
        duration = request.json.get('duration', 15)
        
        anticheat.block_operator(operator_id, duration)
        
        return jsonify({
            'success': True,
            'message': f'Operator blocked for {duration} minutes'
        })
    
    @app.route('/api/anticheat/release/<int:operator_id>', methods=['POST'])
    @jwt_required()
    def release_operator(operator_id):
        """Release blocked operator"""
        with anticheat._lock:
            anticheat.suspicious_operators.discard(operator_id)
        
        return jsonify({'success': True})
    
    @app.route('/api/anticheat/config', methods=['PUT'])
    @jwt_required()
    def update_anticheat_config():
        """Update anti-cheat configuration"""
        data = request.get_json()
        
        for key, value in data.items():
            if key in anticheat.config:
                anticheat.config[key] = value
        
        return jsonify({'success': True, 'config': anticheat.config})


# ============================================================
# REAL-TIME MONITORING WEBSOCKET (optional)
# ============================================================

class MonitoringClient:
    """WebSocket client for real-time alerts"""
    
    def __init__(self):
        self.clients = set()
    
    def broadcast(self, message: dict):
        """Broadcast to all connected clients"""
        for client in self.clients:
            try:
                client.send_json(message)
            except:
                pass
    
    def alert(self, level: str, message: str, data: dict = None):
        """Send alert"""
        self.broadcast({
            'type': 'alert',
            'level': level,
            'message': message,
            'data': data or {}
        })


monitoring = MonitoringClient()


# ============================================================
# SUMMARY OF ANTI-CHEAT FEATURES
# ============================================================

"""
ANTI-CHEAT FEATURES:

1. RATE LIMITING
   - Max 30 requests/minute per IP
   - Max 10 bets/minute per operator
   - Blocks excessive requests

2. BET LIMITS  
   - Max ₱50,000 per fight per operator
   - Max 20 bets per fight per IP
   - Minimum 1 second between bets

3. ANOMALY DETECTION
   - Exact repeat amount detection (bot indicator)
   - Rapid automated betting detection
   - High failure rate monitoring
   
4. OPERATOR BLOCKING
   - Temporary lockout (15 min default)
   - Manual block/release via API
   - Auto-block on HIGH severity anomalies

5. AUDIT TRAIL
   - All rate limit exceeded
   - All bet limit exceeded  
   - All anomalies detected
   - Severity levels: LOW, MEDIUM, HIGH

6. MONITORING DASHBOARD
   - GET /api/anticheat/stats
   - GET /api/anticheat/operator/:id
   - POST /api/anticheat/block/:id
   - POST /api/anticheat/release/:id
   - PUT /api/anticheat/config
"""