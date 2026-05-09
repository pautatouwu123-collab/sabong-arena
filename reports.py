"""
Sabong Daily Sales Report Module
"""

from datetime import datetime, timedelta, date
from flask import Flask, request, jsonify, make_response
import json
import csv
from io import StringIO


class SalesReport:
    """Generate sales reports"""
    
    def __init__(self, db):
        self.db = db
    
    def get_daily_summary(self, target_date=None):
        """Get daily sales summary"""
        if target_date is None:
            target_date = date.today()
        
        from sabong_system import FightArchive
        
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        
        archives = FightArchive.query.filter(
            FightArchive.settled_at >= start,
            FightArchive.settled_at <= end
        ).all()
        
        total_pool = sum(a.total_pool or 0 for a in archives)
        total_meron = sum(a.total_meron or 0 for a in archives)
        total_wala = sum(a.total_wala or 0 for a in archives)
        total_draw = sum(a.total_draw or 0 for a in archives)
        house_cut = sum(a.house_cut or 0 for a in archives)
        
        total_bets = 0
        won_bets = 0
        lost_bets = 0
        draw_bets = 0
        total_payout = 0
        
        for a in archives:
            bets = json.loads(a.bets_snapshot or '[]')
            total_bets += len(bets)
            for b in bets:
                if b.get('status') == 'won':
                    won_bets += 1
                    total_payout += b.get('payout', 0)
                elif b.get('status') == 'lost':
                    lost_bets += 1
                elif b.get('status') == 'draw':
                    draw_bets += 1
        
        return {
            'date': target_date.isoformat(),
            'total_fights': len(archives),
            'total_bets': total_bets,
            'won_bets': won_bets,
            'lost_bets': lost_bets,
            'draw_bets': draw_bets,
            'total_pool': total_pool,
            'total_meron': total_meron,
            'total_wala': total_wala,
            'total_draw': total_draw,
            'house_cut': house_cut,
            'total_payout': total_payout,
            'net_revenue': house_cut,
            'win_rate': (won_bets / total_bets * 100) if total_bets > 0 else 0
        }
    
    def get_operator_summary(self, target_date=None):
        """Get all operators performance"""
        from sabong_system import Operator, Bet
        
        if target_date is None:
            target_date = date.today()
        
        start = datetime.combine(target_date, datetime.min.time())
        end = datetime.combine(target_date, datetime.max.time())
        
        operators = Operator.query.all()
        result = []
        
        for op in operators:
            bets = Bet.query.filter(
                Bet.operator_id == op.id,
                Bet.created_at >= start,
                Bet.created_at <= end
            ).all()
            
            staked = sum(b.amount for b in bets)
            payouts = sum(b.payout for b in bets if b.payout > 0)
            
            result.append({
                'operator_id': op.id,
                'username': op.username,
                'bets_count': len(bets),
                'staked': staked,
                'payouts': payouts,
                'net': staked - payouts
            })
        
        return result


def add_report_routes(app, report_engine):
    """Add report routes"""
    
    @app.route('/api/reports/daily', methods=['GET'])
    def daily_report():
        date_str = request.args.get('date')
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = date.today()
        
        data = report_engine.get_daily_summary(target_date)
        
        if request.args.get('format') == 'csv':
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(['Metric', 'Value'])
            for key, value in data.items():
                writer.writerow([key.replace('_', ' ').title(), value])
            response = make_response(output.getvalue())
            response.headers['Content-Type'] = 'text/csv'
            response.headers['Content-Disposition'] = f'attachment; report_{target_date}.csv'
            return response
        
        return jsonify(data)
    
    @app.route('/api/reports/operator-summary', methods=['GET'])
    def operator_summary():
        date_str = request.args.get('date')
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            target_date = date.today()
        
        data = report_engine.get_operator_summary(target_date)
        return jsonify(data)
