#!/usr/bin/env python3
"""
Sabong Betting System - Complete Backend
A cockfighting betting system with pool-based odds, bayong jackpot, and operator management.
"""

import os
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps

from flask import Flask, request, jsonify, g
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import event
from sqlalchemy.orm import joinedload
import logging

# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sabong-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///sabong.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-key-change-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=1)

db = SQLAlchemy(app)
jwt = JWTManager(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# ENUMS
# ============================================================

class FightStatus(str, Enum):
    UPCOMING = 'upcoming'
    OPEN = 'open'
    LAST_CALL = 'last_call'
    CLOSED = 'closed'
    FINISHED = 'finished'
    CANCELLED = 'cancelled'

class BetSide(str, Enum):
    MERON = 'meron'
    WALA = 'wala'
    DRAW = 'draw'

class BetStatus(str, Enum):
    PENDING = 'pending'
    WON = 'won'
    LOST = 'lost'
    DRAW = 'draw'
    CANCELLED = 'cancelled'
    CLAIMED = 'claimed'

class AuditAction(str, Enum):
    BET_PLACED = 'BET_PLACED'
    PAYOUT_CLAIMED = 'PAYOUT_CLAIMED'
    OPERATOR_LOGIN = 'OPERATOR_LOGIN'
    DUPLICATE_BET_BLOCKED = 'DUPLICATE_BET_BLOCKED'
    FIGHT_CREATED = 'FIGHT_CREATED'
    FIGHT_UPDATED = 'FIGHT_UPDATED'
    FIGHT_SETTLED = 'FIGHT_SETTLED'
    CREDIT_LOADED = 'CREDIT_LOADED'
    CREDIT_WITHDRAWN = 'CREDIT_WITHDRAWN'
    SYSTEM_CONFIG_UPDATED = 'SYSTEM_CONFIG_UPDATED'
    RATE_LIMIT_EXCEEDED = 'RATE_LIMIT_EXCEEDED'
    BET_LIMIT_EXCEEDED = 'BET_LIMIT_EXCEEDED'
    ANOMALY_DETECTED = 'ANOMALY_DETECTED'
    OPERATOR_BLOCKED = 'OPERATOR_BLOCKED'
    OPERATOR_RELEASED = 'OPERATOR_RELEASED'

class AuditSeverity(str, Enum):
    EXTRA_INFO = 'EXTRA_INFO'
    WARNING = 'WARNING'
    ERROR = 'ERROR'

# ============================================================
# DATABASE MODELS
# ============================================================

class Arena(db.Model):
    __tablename__ = 'arenas'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default='SABONG ARENA')
    location = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    fights = db.relationship('Fight', backref='arena', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class SystemConfig(db.Model):
    __tablename__ = 'system_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(500), nullable=False)
    
    @staticmethod
    def get(key, default=None):
        config = SystemConfig.query.filter_by(key=key).first()
        return config.value if config else default
    
    @staticmethod
    def set(key, value):
        config = SystemConfig.query.filter_by(key=key).first()
        if config:
            config.value = str(value)
        else:
            config = SystemConfig(key=key, value=str(value))
            db.session.add(config)
        db.session.commit()


class Operator(db.Model):
    __tablename__ = 'operators'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    pin = db.Column(db.String(64), nullable=False)  # For high-stakes claims
    credit_balance = db.Column(db.Float, default=0)
    total_volume = db.Column(db.Float, default=0)
    total_bets_placed = db.Column(db.Integer, default=0)
    total_payouts = db.Column(db.Float, default=0)
    loaded_total = db.Column(db.Float, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    bets = db.relationship('Bet', backref='operator', lazy='dynamic')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def set_pin(self, pin):
        self.pin = generate_password_hash(pin)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def check_pin(self, pin):
        return check_password_hash(self.pin, pin)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'credit_balance': self.credit_balance,
            'total_volume': self.total_volume,
            'total_bets_placed': self.total_bets_placed,
            'total_payouts': self.total_payouts,
            'loaded_total': self.loaded_total,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


class Fight(db.Model):
    __tablename__ = 'fights'
    
    id = db.Column(db.Integer, primary_key=True)
    fight_number = db.Column(db.Integer, nullable=False)
    arena_id = db.Column(db.Integer, db.ForeignKey('arenas.id'))
    status = db.Column(db.String(20), default=FightStatus.UPCOMING.value)
    
    # Bet tracking
    total_meron = db.Column(db.Float, default=0)
    total_wala = db.Column(db.Float, default=0)
    total_draw = db.Column(db.Float, default=0)
    
    # Rooster names
    meron_name = db.Column(db.String(100))
    wala_name = db.Column(db.String(100))
    
    # Odds (stored for display)
    odds_meron = db.Column(db.Float, default=0)
    odds_wala = db.Column(db.Float, default=0)
    odds_draw = db.Column(db.Float, default=8)  # Fixed from config
    
    # Streaks
    meron_streak = db.Column(db.Integer, default=0)
    wala_streak = db.Column(db.Integer, default=0)
    
    # Winner
    winner = db.Column(db.String(10))  # 'meron', 'wala', 'draw', or None
    
    # Bayong jackpot
    bayong_amount = db.Column(db.Float, default=0)
    bayong_distributed = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    scheduled_at = db.Column(db.DateTime)
    settled_at = db.Column(db.DateTime)
    archived = db.Column(db.Boolean, default=False)
    
    bets = db.relationship('Bet', backref='fight', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        # Get live odds
        odds = self.calculate_odds()
        return {
            'id': self.id,
            'fight_number': self.fight_number,
            'arena_id': self.arena_id,
            'status': self.status,
            'total_meron': self.total_meron,
            'total_wala': self.total_wala,
            'total_draw': self.total_draw,
            'total_pool': self.total_meron + self.total_wala + self.total_draw,
            'odds_meron': odds['meron'],
            'odds_wala': odds['wala'],
            'odds_draw': float(SystemConfig.get('odds_draw', 8)),
            'meron_streak': self.meron_streak,
            'wala_streak': self.wala_streak,
            'winner': self.winner,
            'bayong_amount': self.bayong_amount,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'settled_at': self.settled_at.isoformat() if self.settled_at else None,
            'archived': self.archived
        }
    
    def calculate_odds(self):
        """Calculate pool-based live odds"""
        commission_rate = float(SystemConfig.get('commission_rate', 10)) / 100
        odds_draw = float(SystemConfig.get('odds_draw', 8))
        
        odds_meron = 0
        odds_wala = 0
        
        if self.total_meron > 0 and self.total_wala > 0:
            # Both sides have bets - calculate odds
            payout_pool_meron = self.total_wala * (1 - commission_rate)
            payout_pool_wala = self.total_meron * (1 - commission_rate)
            
            odds_meron = (payout_pool_meron / self.total_meron) + 1
            odds_wala = (payout_pool_wala / self.total_wala) + 1
        elif self.total_meron > 0:
            odds_wala = 1  # Meron heavy - Wala gets 1:1
        elif self.total_wala > 0:
            odds_meron = 1  # Wala heavy - Meron gets 1:1
        
        return {
            'meron': round(odds_meron, 2),
            'wala': round(odds_wala, 2),
            'draw': odds_draw
        }
    
    def update_odds_display(self):
        """Update stored odds values"""
        odds = self.calculate_odds()
        self.odds_meron = odds['meron']
        self.odds_wala = odds['wala']
        self.odds_draw = float(SystemConfig.get('odds_draw', 8))


class Bet(db.Model):
    __tablename__ = 'bets'
    
    id = db.Column(db.Integer, primary_key=True)
    fight_id = db.Column(db.Integer, db.ForeignKey('fights.id'), nullable=False)
    operator_id = db.Column(db.Integer, db.ForeignKey('operators.id'), nullable=False)
    
    amount = db.Column(db.Float, nullable=False)
    side = db.Column(db.String(10), nullable=False)  # 'meron', 'wala', 'draw'
    
    # Computed at settlement
    payout = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default=BetStatus.PENDING.value)
    
    # Metadata
    ticket_number = db.Column(db.String(20), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    claimed_at = db.Column(db.DateTime)
    
    def generate_ticket_number(self):
        """Generate unique ticket number"""
        return f"TKT{datetime.utcnow().strftime('%Y%m%d')}{self.id:06d}"
    
    def to_dict(self):
        return {
            'id': self.id,
            'fight_id': self.fight_id,
            'operator_id': self.operator_id,
            'amount': self.amount,
            'side': self.side,
            'payout': self.payout,
            'status': self.status,
            'ticket_number': self.ticket_number,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'claimed_at': self.claimed_at.isoformat() if self.claimed_at else None
        }


class FightArchive(db.Model):
    """Immutable snapshot of fight after settlement"""
    __tablename__ = 'fight_archives'
    
    id = db.Column(db.Integer, primary_key=True)
    fight_id = db.Column(db.Integer, nullable=False)
    fight_number = db.Column(db.Integer, nullable=False)
    arena_id = db.Column(db.Integer)
    arena_name = db.Column(db.String(100))
    
    status = db.Column(db.String(20))
    winner = db.Column(db.String(10))
    
    total_meron = db.Column(db.Float)
    total_wala = db.Column(db.Float)
    total_draw = db.Column(db.Float)
    total_pool = db.Column(db.Float)
    house_cut = db.Column(db.Float)
    payout_pool = db.Column(db.Float)
    
    meron_streak = db.Column(db.Integer)
    wala_streak = db.Column(db.Integer)
    bayong_amount = db.Column(db.Float)
    
    settled_at = db.Column(db.DateTime)
    archived_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    bets_snapshot = db.Column(db.Text)  # JSON of all bets
    
    def to_dict(self):
        return {
            'id': self.id,
            'fight_id': self.fight_id,
            'fight_number': self.fight_number,
            'arena_name': self.arena_name,
            'status': self.status,
            'winner': self.winner,
            'total_meron': self.total_meron,
            'total_wala': self.total_wala,
            'total_draw': self.total_draw,
            'total_pool': self.total_pool,
            'house_cut': self.house_cut,
            'payout_pool': self.payout_pool,
            'meron_streak': self.meron_streak,
            'wala_streak': self.wala_streak,
            'bayong_amount': self.bayong_amount,
            'settled_at': self.settled_at.isoformat() if self.settled_at else None,
            'archived_at': self.archived_at.isoformat() if self.archived_at else None
        }


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    action = db.Column(db.String(50), nullable=False)
    severity = db.Column(db.String(20), default=AuditSeverity.EXTRA_INFO.value)
    
    operator_id = db.Column(db.Integer, db.ForeignKey('operators.id'))
    fight_id = db.Column(db.Integer)
    bet_id = db.Column(db.Integer)
    
    details = db.Column(db.Text)  # JSON details
    
    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'action': self.action,
            'severity': self.severity,
            'operator_id': self.operator_id,
            'fight_id': self.fight_id,
            'bet_id': self.bet_id,
            'details': self.details
        }


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def log_audit(action, operator_id=None, fight_id=None, bet_id=None, 
             severity=AuditSeverity.EXTRA_INFO.value, details=None):
    """Create audit log entry"""
    log = AuditLog(
        action=action,
        operator_id=operator_id,
        fight_id=fight_id,
        bet_id=bet_id,
        severity=severity,
        details=str(details) if details else None
    )
    db.session.add(log)
    db.session.commit()
    return log


def check_betting_enabled():
    """Check if betting is enabled globally"""
    enabled = SystemConfig.get('betting_enabled', 'true').lower()
    return enabled == 'true'


def check_duplicate_bet(fight_id, operator_id, side):
    """Check for duplicate bet within 3 seconds"""
    recent = datetime.utcnow() - timedelta(seconds=3)
    duplicate = Bet.query.filter(
        Bet.fight_id == fight_id,
        Bet.operator_id == operator_id,
        Bet.side == side,
        Bet.created_at >= recent
    ).first()
    return duplicate is not None


def generate_ticket():
    """Generate unique ticket number"""
    import random
    import string
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"SB{timestamp}{random_part}"


# ============================================================
# API ROUTES - AUTH
# ============================================================

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Operator login"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    operator = Operator.query.filter_by(username=username).first()
    if not operator or not operator.check_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    if not operator.is_active:
        return jsonify({'error': 'Account is disabled'}), 403
    
    operator.last_login = datetime.utcnow()
    db.session.commit()
    
    # Create token - identity must be a string
    access_token = create_access_token(identity=str(operator.id))
    
    log_audit(AuditAction.OPERATOR_LOGIN, operator_id=operator.id, 
             details=f'Operator {username} logged in')
    
    return jsonify({
        'access_token': access_token,
        'operator': operator.to_dict()
    })


# ============================================================
# API ROUTES - SYSTEM CONFIG
# ============================================================

@app.route('/api/config', methods=['GET'])
@jwt_required()
def get_config():
    """Get all system configuration"""
    configs = SystemConfig.query.all()
    return jsonify({c.key: c.value for c in configs})


@app.route('/api/config', methods=['POST'])
@jwt_required()
def update_config():
    """Update system configuration"""
    data = request.get_json()
    
    for key, value in data.items():
        SystemConfig.set(key, value)
    
    log_audit(AuditAction.SYSTEM_CONFIG_UPDATED, 
              details=f'Updated config: {list(data.keys())}')
    
    return jsonify({'success': True, 'config': data})


# ============================================================
# API ROUTES - FIGHTS
# ============================================================

@app.route('/api/fights', methods=['GET'])
@jwt_required()
def get_fights():
    """Get all fights"""
    status = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)
    
    query = Fight.query
    if status:
        query = query.filter_by(status=status)
    
    fights = query.order_by(Fight.id.desc()).limit(limit).all()
    return jsonify([f.to_dict() for f in fights])


@app.route('/api/fights/<int:fight_id>', methods=['GET'])
@jwt_required()
def get_fight(fight_id):
    """Get specific fight"""
    fight = Fight.query.get_or_404(fight_id)
    return jsonify(fight.to_dict())


@app.route('/api/fights', methods=['POST'])
@jwt_required()
def create_fight():
    """Create new fight"""
    data = request.get_json()
    
    # Get next fight number
    last_fight = Fight.query.order_by(Fight.fight_number.desc()).first()
    fight_number = (last_fight.fight_number + 1) if last_fight else 1
    
    # Get or create default arena
    arena = Arena.query.first()
    if not arena:
        arena = Arena(name=SystemConfig.get('arena_name', 'SABONG ARENA'))
        db.session.add(arena)
        db.session.commit()
    
    fight = Fight(
        fight_number=fight_number,
        arena_id=arena.id,
        status=FightStatus.UPCOMING.value,
        scheduled_at=data.get('scheduled_at')
    )
    
    # Initialize bayong
    bayong_increment = float(SystemConfig.get('bayong_increment', 100))
    fight.bayong_amount = bayong_increment
    
    db.session.add(fight)
    db.session.commit()
    
    log_audit(AuditAction.FIGHT_CREATED, fight_id=fight.id,
              details=f'Created fight #{fight_number}')
    
    return jsonify(fight.to_dict()), 201


@app.route('/api/fights/<int:fight_id>/status', methods=['PUT'])
@jwt_required()
def update_fight_status(fight_id):
    """Update fight status"""
    data = request.get_json()
    new_status = data.get('status')
    
    fight = Fight.query.get_or_404(fight_id)
    old_status = fight.status
    
    valid_transitions = {
        FightStatus.UPCOMING: [FightStatus.OPEN],
        FightStatus.OPEN: [FightStatus.LAST_CALL, FightStatus.CLOSED, FightStatus.CANCELLED],
        FightStatus.LAST_CALL: [FightStatus.CLOSED, FightStatus.CANCELLED],
        FightStatus.CLOSED: [FightStatus.FINISHED, FightStatus.CANCELLED],
    }
    
    if new_status not in valid_transitions.get(old_status, []):
        return jsonify({'error': f'Invalid status transition from {old_status} to {new_status}'}), 400
    
    fight.status = new_status
    db.session.commit()
    
    log_audit(AuditAction.FIGHT_UPDATED, fight_id=fight.id,
              details=f'Status changed from {old_status} to {new_status}')
    
    return jsonify(fight.to_dict())


@app.route('/api/fights/<int:fight_id>/settle', methods=['POST'])
@jwt_required()
def settle_fight(fight_id):
    """Declare winner and settle all bets"""
    data = request.get_json()
    winner = data.get('winner')  # 'meron', 'wala', 'draw'
    
    fight = Fight.query.get_or_404(fight_id)
    
    if fight.status != FightStatus.CLOSED.value:
        return jsonify({'error': 'Fight must be closed before settling'}), 400
    
    if winner not in ['meron', 'wala', 'draw']:
        return jsonify({'error': 'Invalid winner'}), 400
    
    # Handle declare winner logic
    handle_declare_winner(fight, winner)
    
    return jsonify(fight.to_dict())


@app.route('/api/fights/<int:fight_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_fight(fight_id):
    """Cancel a fight and refund all bets"""
    fight = Fight.query.get_or_404(fight_id)
    
    if fight.status in [FightStatus.FINISHED.value, FightStatus.CANCELLED.value]:
        return jsonify({'error': 'Fight already settled'}), 400
    
    fight.status = FightStatus.CANCELLED.value
    fight.winner = None
    
    # Refund all pending bets
    bets = Bet.query.filter_by(fight_id=fight_id, status=BetStatus.PENDING.value).all()
    for bet in bets:
        bet.status = BetStatus.CANCELLED.value
        bet.payout = bet.amount  # Full refund
    
    db.session.commit()
    
    log_audit(AuditAction.FIGHT_UPDATED, fight_id=fight_id,
              details='Fight cancelled - all bets refunded')
    
    return jsonify(fight.to_dict())


# ============================================================
# BETTING LOGIC
# ============================================================

def handle_declare_winner(fight, winner):
    """Core bet settlement logic"""
    # Get all pending bets
    bets = Bet.query.filter_by(fight_id=fight.id, status=BetStatus.PENDING.value).all()
    
    # Calculate pools
    total_meron = fight.total_meron
    total_wala = fight.total_wala
    total_draw = fight.total_draw
    total_pool = total_meron + total_wala + total_draw
    
    # Calculate commission and payout pool
    commission_rate = float(SystemConfig.get('commission_rate', 10)) / 100
    house_cut = total_pool * commission_rate
    payout_pool = total_pool - house_cut
    
    # Get bayong settings
    bayong_increment = float(SystemConfig.get('bayong_increment', 100))
    bayong_consecutive_wins = int(SystemConfig.get('bayong_consecutive_wins', 10))
    
    # Determine if bayong triggers
    bayong_triggered = False
    if winner == 'meron' and fight.meron_streak >= bayong_consecutive_wins:
        bayong_triggered = True
    elif winner == 'wala' and fight.wala_streak >= bayong_consecutive_wins:
        bayong_triggered = True
    
    # Calculate current bayong
    current_bayong = fight.bayong_amount
    if bayong_triggered:
        # Add pending bayong to current
        current_bayong = fight.bayong_amount + bayong_increment
    
    # Get winning pool
    winning_pool = 0
    if winner == 'meron':
        winning_pool = total_meron
    elif winner == 'wala':
        winning_pool = total_wala
    elif winner == 'draw':
        winning_pool = total_draw
    
    # Calculate payouts for each bet
    odds_draw = float(SystemConfig.get('odds_draw', 8))
    
    for bet in bets:
        if winner == 'draw':
            if bet.side == 'draw':
                # Draw bet on draw: fixed multiplier
                bet.payout = bet.amount * odds_draw
                bet.status = BetStatus.WON.value
            else:
                # Non-draw bet on draw: refund
                bet.payout = bet.amount
                bet.status = BetStatus.DRAW.value
        elif bet.side == winner:
            # Winning side
            if winning_pool > 0:
                # Proportional share
                bet.payout = (bet.amount / winning_pool) * payout_pool
                # Add bayong share if triggered
                if bayong_triggered and current_bayong > 0:
                    bayong_share = (bet.amount / winning_pool) * current_bayong
                    bet.payout += bayong_share
            else:
                bet.payout = 0
            bet.status = BetStatus.WON.value
        elif bet.side == 'draw':
            # Draw bet on non-draw: refund
            bet.payout = bet.amount
            bet.status = BetStatus.DRAW.value
        else:
            # Lost
            bet.payout = 0
            bet.status = BetStatus.LOST.value
    
    # Update fight
    fight.winner = winner
    fight.status = FightStatus.FINISHED.value
    fight.settled_at = datetime.utcnow()
    fight.archived = True
    
    # Update streaks and bayong
    if winner == 'meron':
        fight.meron_streak += 1
        fight.wala_streak = 0
        if bayong_triggered:
            fight.bayong_amount = bayong_increment  # Reset after distribution
        else:
            fight.bayong_amount += bayong_increment
    elif winner == 'wala':
        fight.wala_streak += 1
        fight.meron_streak = 0
        if bayong_triggered:
            fight.bayong_amount = bayong_increment
        else:
            fight.bayong_amount += bayong_increment
    else:
        # Draw - reset streaks
        fight.meron_streak = 0
        fight.wala_streak = 0
        fight.bayong_amount += bayong_increment
    
    db.session.commit()
    
    # Create archive snapshot
    create_fight_archive(fight, total_pool, house_cut, payout_pool, bets)
    
    log_audit(AuditAction.FIGHT_SETTLED, fight_id=fight.id,
              details=f'Winner: {winner}, Pool: {total_pool}, House Cut: {house_cut}')


def create_fight_archive(fight, total_pool, house_cut, payout_pool, bets):
    """Create immutable archive of settled fight"""
    arena = Arena.query.get(fight.arena_id) if fight.arena_id else None
    
    import json
    bets_data = [
        {
            'id': b.id,
            'operator_id': b.operator_id,
            'amount': b.amount,
            'side': b.side,
            'payout': b.payout,
            'status': b.status
        }
        for b in bets
    ]
    
    archive = FightArchive(
        fight_id=fight.id,
        fight_number=fight.fight_number,
        arena_id=fight.arena_id,
        arena_name=arena.name if arena else None,
        status=fight.status,
        winner=fight.winner,
        total_meron=fight.total_meron,
        total_wala=fight.total_wala,
        total_draw=fight.total_draw,
        total_pool=total_pool,
        house_cut=house_cut,
        payout_pool=payout_pool,
        meron_streak=fight.meron_streak,
        wala_streak=fight.wala_streak,
        bayong_amount=fight.bayong_amount,
        settled_at=fight.settled_at,
        bets_snapshot=json.dumps(bets_data)
    )
    
    db.session.add(archive)
    db.session.commit()


# ============================================================
# API ROUTES - BETS
# ============================================================

@app.route('/api/bets', methods=['POST'])
@jwt_required()
def place_bet():
    """Place a bet"""
    operator_id = int(get_jwt_identity())
    operator = Operator.query.get(operator_id)
    
    data = request.get_json()
    fight_id = data.get('fight_id')
    amount = data.get('amount')
    side = data.get('side')  # 'meron', 'wala', 'draw'
    
    # Validate input
    if not all([fight_id, amount, side]):
        return jsonify({'error': 'fight_id, amount, and side required'}), 400
    
    if side not in ['meron', 'wala', 'draw']:
        return jsonify({'error': 'Invalid side'}), 400
    
    if amount <= 0:
        return jsonify({'error': 'Amount must be positive'}), 400
    
    # Check betting enabled
    if not check_betting_enabled():
        return jsonify({'error': 'Betting is currently disabled'}), 400
    
    # Get fight
    fight = Fight.query.get_or_404(fight_id)
    
    if fight.status not in [FightStatus.OPEN.value, FightStatus.LAST_CALL.value]:
        return jsonify({'error': f'Cannot bet - fight status is {fight.status}'}), 400
    
    # Check credit
    if operator.credit_balance < amount:
        return jsonify({'error': 'Insufficient credit'}), 400
    
    # Check duplicate bet (anti-cheat)
    if check_duplicate_bet(fight_id, operator_id, side):
        log_audit(AuditAction.DUPLICATE_BET_BLOCKED, operator_id=operator_id, 
                  fight_id=fight_id, severity=AuditSeverity.WARNING.value,
                  details=f'Duplicate bet blocked: fight={fight_id}, side={side}')
        return jsonify({'error': 'Duplicate bet detected. Please wait.'}), 400
    
    # Create bet
    bet = Bet(
        fight_id=fight_id,
        operator_id=operator_id,
        amount=amount,
        side=side
    )
    bet.ticket_number = generate_ticket()
    
    db.session.add(bet)
    db.session.flush()  # Get bet.id
    
    # Update fight pools
    if side == 'meron':
        fight.total_meron += amount
    elif side == 'wala':
        fight.total_wala += amount
    elif side == 'draw':
        fight.total_draw += amount
    
    fight.update_odds_display()
    
    # Update operator
    operator.credit_balance -= amount
    operator.total_volume += amount
    operator.total_bets_placed += 1
    
    db.session.commit()
    
    # Get updated odds
    odds = fight.calculate_odds()
    
    log_audit(AuditAction.BET_PLACED, operator_id=operator_id, 
             fight_id=fight_id, bet_id=bet.id,
             details=f'Ticket: {bet.ticket_number}, Side: {side}, Amount: {amount}')
    
    return jsonify({
        'bet': bet.to_dict(),
        'fight': fight.to_dict(),
        'odds': odds
    }), 201


@app.route('/api/bets/<int:bet_id>/claim', methods=['POST'])
@jwt_required()
def claim_payout(bet_id):
    """Claim bet payout"""
    operator_id = int(get_jwt_identity())
    operator = Operator.query.get(operator_id)
    
    data = request.get_json()
    pin = data.get('pin')  # Required for high-stakes
    
    bet = Bet.query.get_or_404(bet_id)
    
    if bet.operator_id != operator_id:
        return jsonify({'error': 'Bet not owned by operator'}), 403
    
    if bet.status not in [BetStatus.WON.value, BetStatus.DRAW.value, BetStatus.CANCELLED.value]:
        return jsonify({'error': 'Bet not eligible for payout'}), 400
    
    if bet.status == BetStatus.CLAIMED.value:
        return jsonify({'error': 'Payout already claimed'}), 400
    
    # High-stakes check
    high_stakes_threshold = float(SystemConfig.get('high_stakes_threshold', 5000))
    if bet.payout > high_stakes_threshold:
        if not pin or not operator.check_pin(pin):
            return jsonify({'error': 'PIN required for high-stakes claim'}), 401
    
    # Process payout
    operator.credit_balance += bet.payout
    operator.total_payouts += bet.payout
    
    bet.status = BetStatus.CLAIMED.value
    bet.claimed_at = datetime.utcnow()
    
    db.session.commit()
    
    log_audit(AuditAction.PAYOUT_CLAIMED, operator_id=operator_id,
              bet_id=bet_id,
              details=f'Claimed: {bet.payout}')
    
    return jsonify({
        'payout': bet.payout,
        'credit_balance': operator.credit_balance
    })


@app.route('/api/bets/operator/<int:operator_id>', methods=['GET'])
@jwt_required()
def get_operator_bets(operator_id):
    """Get bets for an operator"""
    current_operator_id = int(get_jwt_identity())
    
    if current_operator_id != operator_id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    status = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)
    
    query = Bet.query.filter_by(operator_id=operator_id)
    if status:
        query = query.filter_by(status=status)
    
    bets = query.order_by(Bet.created_at.desc()).limit(limit).all()
    return jsonify([b.to_dict() for b in bets])


# ============================================================
# API ROUTES - OPERATORS
# ============================================================

@app.route('/api/operators', methods=['POST'])
@jwt_required()
def create_operator():
    """Create new operator (admin only - should have admin check)"""
    data = request.get_json()
    
    username = data.get('username')
    password = data.get('password')
    pin = data.get('pin')
    initial_credit = data.get('initial_credit', 0)
    
    if not all([username, password, pin]):
        return jsonify({'error': 'username, password, and pin required'}), 400
    
    if Operator.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    operator = Operator(
        username=username,
        credit_balance=initial_credit,
        loaded_total=initial_credit
    )
    operator.set_password(password)
    operator.set_pin(pin)
    
    db.session.add(operator)
    db.session.commit()
    
    return jsonify(operator.to_dict()), 201


@app.route('/api/operators/<int:operator_id>/load', methods=['POST'])
@jwt_required()
def load_credits(operator_id):
    """Load credits to operator account"""
    operator = Operator.query.get_or_404(operator_id)
    calling_operator_id = int(get_jwt_identity())
    
    data = request.get_json()
    amount = data.get('amount')
    
    if not amount or amount <= 0:
        return jsonify({'error': 'Invalid amount'}), 400
    
    operator.credit_balance += amount
    operator.loaded_total += amount
    
    db.session.commit()
    
    log_audit(AuditAction.CREDIT_LOADED, operator_id=operator_id,
              details=f'Loaded: {amount}')
    
    return jsonify({
        'success': True,
        'credit_balance': operator.credit_balance
    })


@app.route('/api/operators/<int:operator_id>/withdraw', methods=['POST'])
@jwt_required()
def withdraw_credits(operator_id):
    """Withdraw credits from operator account"""
    operator = Operator.query.get_or_404(operator_id)
    calling_operator_id = int(get_jwt_identity())
    
    data = request.get_json()
    amount = data.get('amount')
    
    if not amount or amount <= 0:
        return jsonify({'error': 'Invalid amount'}), 400
    
    if operator.credit_balance < amount:
        return jsonify({'error': 'Insufficient credit'}), 400
    
    operator.credit_balance -= amount
    
    db.session.commit()
    
    log_audit(AuditAction.CREDIT_WITHDRAWN, operator_id=operator_id,
              details=f'Withdrawn: {amount}')
    
    return jsonify({
        'success': True,
        'credit_balance': operator.credit_balance
    })


@app.route('/api/operators', methods=['GET'])
@jwt_required()
def list_operators():
    """List all operators"""
    limit = request.args.get('limit', 50, type=int)
    operators = Operator.query.order_by(Operator.id.desc()).limit(limit).all()
    return jsonify([o.to_dict() for o in operators])


@app.route('/api/operators/me', methods=['GET'])
@jwt_required()
def get_current_operator():
    """Get current operator info"""
    operator_id = int(get_jwt_identity())
    operator = Operator.query.get(operator_id)
    return jsonify(operator.to_dict())


# ============================================================
# API ROUTES - ARENA
# ============================================================

@app.route('/api/arena', methods=['GET'])
def get_arena():
    """Get current arena info (public)"""
    arena = Arena.query.first()
    if not arena:
        arena = Arena(name=SystemConfig.get('arena_name', 'SABONG ARENA'))
        db.session.add(arena)
        db.session.commit()
    return jsonify(arena.to_dict())


# ============================================================
# API ROUTES - AUDIT
# ============================================================

@app.route('/api/audit', methods=['GET'])
@jwt_required()
def get_audit_logs():
    """Get audit logs"""
    limit = request.args.get('limit', 100, type=int)
    action = request.args.get('action')
    operator_id = request.args.get('operator_id', type=int)
    
    query = AuditLog.query
    if action:
        query = query.filter_by(action=action)
    if operator_id:
        query = query.filter_by(operator_id=operator_id)
    
    logs = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return jsonify([l.to_dict() for l in logs])


@app.route('/api/fight-archives', methods=['GET'])
@jwt_required()
def get_fight_archives():
    """Get fight archives"""
    limit = request.args.get('limit', 50, type=int)
    
    archives = FightArchive.query.order_by(
        FightArchive.archived_at.desc()
    ).limit(limit).all()
    
    return jsonify([a.to_dict() for a in archives])


# ============================================================
# API ROUTES - LIVE ODDS (Public)
# ============================================================

@app.route('/api/live/odds', methods=['GET'])
def get_live_odds():
    """Get live odds for all active fights (public endpoint)"""
    fights = Fight.query.filter(
        Fight.status.in_([FightStatus.OPEN.value, FightStatus.LAST_CALL.value])
    ).all()
    
    result = []
    for fight in fights:
        odds = fight.calculate_odds()
        result.append({
            'fight_number': fight.fight_number,
            'status': fight.status,
            'odds_meron': odds['meron'],
            'odds_wala': odds['wala'],
            'odds_draw': odds['draw'],
            'total_pool': fight.total_meron + fight.total_wala + fight.total_draw
        })
    
    return jsonify(result)


# Import anti-cheat
from anticheat import add_anticheat_routes, anticheat, rate_limit_check, bet_limits_check

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    """Initialize database with default values"""
    db.create_all()
    
    # Default configurations
    defaults = {
        'commission_rate': '10',
        'odds_draw': '8',
        'bayong_increment': '100',
        'bayong_consecutive_wins': '10',
        'betting_enabled': 'true',
        'high_stakes_threshold': '5000',
        'arena_name': 'SABONG ARENA'
    }
    
    for key, value in defaults.items():
        if not SystemConfig.get(key):
            SystemConfig.set(key, value)
    
    # Create default arena if none
    if not Arena.query.first():
        arena = Arena(name=defaults['arena_name'])
        db.session.add(arena)
        db.session.commit()
    
    # Create default admin operator if none
    if not Operator.query.first():
        admin = Operator(
            username='admin',
            credit_balance=100000,
            loaded_total=100000
        )
        admin.set_password('admin123')
        admin.set_pin('1234')
        db.session.add(admin)
        db.session.commit()
        logger.info('Created default admin operator: username=admin, password=admin123, pin=1234')
    
    # Add anti-cheat routes
    add_anticheat_routes(app)
    
    # Add report routes
    from reports import SalesReport, add_report_routes as add_reporting_routes
    report_engine = SalesReport(db)
    add_reporting_routes(app, report_engine)


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    with app.app_context():
        init_db()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)