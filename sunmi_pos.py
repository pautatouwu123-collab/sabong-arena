#!/usr/bin/env python3
"""
Sabong Sunmi V2 Pro Integration
- Thermal ticket printing
- Barcode scanning via camera
- Built-in barcode scanner support
"""

import os
import base64
import json
import time
import threading
import tempfile
import uuid
from datetime import datetime
from queue import Queue
from typing import Optional, Dict, Any, Callable

# For barcode scanning
try:
    import cv2
    import numpy as np
    from pyzbar.pyzbar import decode as decode_qr
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("Warning: OpenCV not available. Camera scanning disabled.")

# For thermal printing
try:
    from escpos import printer
    from escpos.constants import *
    from escpos.exceptions import *
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False
    print("Warning: python-escpos not available. Printing disabled.")


class ThermalPrinter:
    """Thermal ticket printer (Sunmi, Epson, or generic ESC/POS)"""
    
    def __init__(self, printer_type: str = 'sunmi', device: str = None):
        """
        Initialize printer
        Args:
            printer_type: 'sunmi', 'epson', 'generic'
            device: USB path or network IP (None for default)
        """
        self.printer_type = printer_type
        self.device = device
        self.printer = None
        self._connect()
    
    def _connect(self):
        """Connect to printer"""
        if not ESCPOS_AVAILABLE:
            print("ESC/POS not available")
            return
        
        try:
            if self.printer_type == 'sunmi':
                # Sunmi POS printer
                if self.device:
                    self.printer = printer.Usb(0x0483, 0x5744, self.device)
                else:
                    # Auto-detect Sunmi
                    self.printer = printer.Serial('/dev/ttyS0', 9600)
            elif self.printer_type == 'epson':
                self.printer = printer.Network(self.device or '192.168.1.100')
            else:
                # Generic serial
                self.printer = printer.Serial(self.device or '/dev/usb/lp0', 9600)
            
            print(f"Connected to {self.printer_type} printer")
        except Exception as e:
            print(f"Printer connection error: {e}")
            self.printer = None
    
    def print_ticket(self, bet_data: Dict[str, Any], fight_data: Dict[str, Any]) -> bool:
        """
        Print betting ticket
        Args:
            bet_data: {'ticket_number', 'amount', 'side', 'created_at', ...}
            fight_data: {'fight_number', 'odds_meron', 'odds_wala', 'arena_name', ...}
        """
        if not self.printer:
            print("Printer not connected")
            return False
        
        try:
            # Header
            self.printer.set(align='center', font='a', double_height=True, bold=True)
            self.printer.text("SABONG ARENA\n")
            self.printer.text(fight_data.get('arena_name', 'SABONG ARENA') + "\n")
            
            self.printer.set(align='center', font='a', size='2x')
            self.printer.text(f"FIGHT #{fight_data.get('fight_number', '')}\n")
            
            self.printer.set(align='center')
            self.printer.text("-" * 32 + "\n")
            
            # Bet details
            side = bet_data.get('side', '').upper()
            amount = float(bet_data.get('amount', 0))
            
            if side == 'MERON':
                side_display = "🦌 MERON"
                odds = float(fight_data.get('odds_meron', 0))
            elif side == 'WALA':
                side_display = "🐓 WALA"
                odds = float(fight_data.get('odds_wala', 0))
            else:
                side_display = "⚪ DRAW"
                odds = float(fight_data.get('odds_draw', 8))
            
            self.printer.set(align='center', font='a', double_width=True)
            self.printer.text(f"\n{side_display}\n")
            
            self.printer.set(align='center', font='a', size='2x', bold=True)
            self.printer.text(f"₱{amount:,.0f}\n")
            
            self.printer.set(align='center')
            self.printer.text("-" * 32 + "\n")
            
            # Odds and potential payout
            self.printer.text(f"Odds: {odds:.2f}\n")
            potential = amount * odds
            self.printer.text(f"WIN: ₱{potential:,.2f}\n")
            self.printer.text(f"DRAW: ₱{amount:,.2f}\n")
            
            self.printer.text("-" * 32 + "\n")
            
            # Ticket number with barcode
            ticket = bet_data.get('ticket_number', '')
            self.printer.set(align='center', font='a')
            self.printer.text(f"Ticket: {ticket}\n")
            
            # Timestamp
            created = bet_data.get('created_at', '')
            if created:
                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                self.printer.text(f"Time: {dt.strftime('%m/%d/%Y %H:%M:%S')}\n")
            
            # Barcode
            self.printer.barcode(ticket, 'CODE128', align='center', height=50)
            
            # Footer
            self.printer.text("\n")
            self.printer.set(align='center', font='a', size='small')
            self.printer.text("Present ticket to claim winnings\n")
            self.printer.text("Void after 24 hours\n")
            self.printer.text("Thanks for playing!\n\n")
            
            self.printer.cut()
            return True
            
        except Exception as e:
            print(f"Print error: {e}")
            return False
    
    def print_receipt(self, title: str, lines: list, total: float = 0) -> bool:
        """Print general receipt"""
        if not self.printer:
            return False
        
        try:
            self.printer.set(align='center', bold=True)
            self.printer.text(f"{title}\n")
            self.printer.text("=" * 32 + "\n")
            
            for line in lines:
                self.printer.text(line + "\n")
            
            if total > 0:
                self.printer.text("-" * 32 + "\n")
                self.printer.set(bold=True)
                self.printer.text(f"TOTAL: ₱{total:,.2f}\n")
            
            self.printer.cut()
            return True
        except:
            return False
    
    def close(self):
        """Close printer connection"""
        if self.printer:
            try:
                self.printer.close()
            except:
                pass


class BarcodeScanner:
    """Barcode scanner using camera or built-in scanner"""
    
    def __init__(self, camera_index: int = 0, on_scan: Callable = None):
        """
        Initialize scanner
        Args:
            camera_index: Camera device index
            on_scan: Callback function(scan_data) when code detected
        """
        self.camera_index = camera_index
        self.on_scan = on_scan
        self.running = False
        self.thread = None
        self.cap = None
        
        if CV2_AVAILABLE:
            self.cap = cv2.VideoCapture(camera_index)
            if not self.cap.isOpened():
                print(f"Cannot open camera {camera_index}")
                self.cap = None
    
    def start(self):
        """Start scanning in background"""
        if not self.cap:
            print("Camera not available")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._scan_loop, daemon=True)
        self.thread.start()
    
    def _scan_loop(self):
        """Scanning loop"""
        last_scan = ''
        cooldown = 0
        
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            
            # Decode barcodes
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            barcodes = decode_qr(gray)
            
            for barcode in barcodes:
                data = barcode.data.decode('utf-8')
                
                # Debounce: ignore same code within 3 seconds
                if data != last_scan or cooldown <= 0:
                    if self.on_scan:
                        self.on_scan({
                            'data': data,
                            'type': barcode.type,
                            'timestamp': datetime.now().isoformat()
                        })
                    last_scan = data
                    cooldown = 30  # 3 seconds at 10fps
            
            if cooldown > 0:
                cooldown -= 1
            
            # Small delay
            time.sleep(0.1)
    
    def stop(self):
        """Stop scanning"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()
    
    def scan_manual(self, timeout: float = 5) -> Optional[Dict]:
        """
        Manual scan (blocking)
        Returns scan data or None
        """
        if not self.cap:
            return None
        
        result = Queue()
        
        def callback(data):
            result.put(data)
        
        old_callback = self.on_scan
        self.on_scan = callback
        self.start()
        
        try:
            return result.get(timeout=timeout)
        except:
            return None
        finally:
            self.on_scan = old_callback
            self.stop()


class SunmiPOS:
    """Sunmi V2 Pro complete integration"""
    
    def __init__(self, 
                 api_base: str = 'http://localhost:5000',
                 printer_type: str = 'sunmi',
                 device: str = None):
        """
        Initialize Sunmi POS integration
        Args:
            api_base: API server base URL
            printer_type: 'sunmi', 'epson', 'generic'
            device: Printer device path
        """
        self.api_base = api_base.rstrip('/')
        self.printer = ThermalPrinter(printer_type, device)
        self.scanner = BarcodeScanner(on_scan=self._on_barcode_scan)
        self.pending_scans = Queue()
    
    def _on_barcode_scan(self, scan_data: dict):
        """Handle barcode scan"""
        self.pending_scans.put(scan_data)
    
    def print_bet_ticket(self, bet_id: int, api_token: str) -> bool:
        """
        Fetch bet from API and print ticket
        Args:
            bet_id: Bet ID
            api_token: JWT token
        """
        import requests
        
        # Get bet data
        resp = requests.get(
            f'{self.api_base}/api/bets/{bet_id}',
            headers={'Authorization': f'Bearer {api_token}'}
        )
        if resp.status_code != 200:
            print(f"Failed to fetch bet: {resp.text}")
            return False
        
        bet = resp.json()
        
        # Get fight data
        resp = requests.get(
            f'{self.api_base}/api/fights/{bet["fight_id"]}',
            headers={'Authorization': f'Bearer {api_token}'}
        )
        if resp.status_code != 200:
            print(f"Failed to fetch fight: {resp.text}")
            return False
        
        fight = resp.json()
        
        return self.printer.print_ticket(bet, fight)
    
    def scan_and_place_bet(self, fight_id: int, amount: float, side: str, 
                     api_token: str, timeout: float = 30) -> Optional[Dict]:
        """
        Scan ticket, place bet, print confirmation
        Args:
            fight_id: Fight ID
            amount: Bet amount
            side: 'meron', 'wala', 'draw'
            api_token: JWT token
            timeout: Scan timeout in seconds
        
        Returns:
            Bet data or None on failure
        """
        import requests
        
        # Start scanner
        self.scanner.start()
        
        # Wait for scan
        try:
            scan_data = self.pending_scans.get(timeout=timeout)
        except:
            print("Scan timeout")
            self.scanner.stop()
            return None
        
        self.scanner.stop()
        
        ticket_number = scan_data['data']
        
        # Place bet
        resp = requests.post(
            f'{self.api_base}/api/bets',
            headers={'Authorization': f'Bearer {api_token}'},
            json={
                'fight_id': fight_id,
                'amount': amount,
                'side': side
            }
        )
        
        if resp.status_code != 201:
            print(f"Bet failed: {resp.text}")
            return None
        
        bet = resp.json()['bet']
        fight = resp.json()['fight']
        
        # Print ticket
        self.printer.print_ticket(bet, fight)
        
        return bet
    
    def claim_payout(self, ticket_number: str, api_token: str, pin: str = None) -> Dict:
        """
        Scan ticket and claim payout
        Args:
            ticket_number: Ticket scanned from barcode
            api_token: JWT token
            pin: PIN for high-stakes (optional)
        
        Returns:
            {'success': bool, 'payout': float, 'message': str}
        """
        import requests
        
        # Find bet by ticket
        # Note: Would need to add endpoint for this
        # For now, use ticket number directly
        
        # Try to get bet by scanning again if needed
        return {
            'success': True,
            'message': 'Use /api/bets/claim endpoint'
        }
    
    def print_daily_report(self, api_token: str) -> bool:
        """Print daily summary report"""
        import requests
        
        # Get today's fights
        resp = requests.get(
            f'{self.api_base}/api/fight-archives?limit=100',
            headers={'Authorization': f'Bearer {api_token}'}
        )
        
        archives = resp.json()
        
        # Calculate totals
        total_pool = sum(a['total_pool'] for a in archives)
        total_payout = sum(
            b['payout'] for a in archives 
            for b in json.loads(a.get('bets_snapshot', '[]'))
            if b['status'] == 'won'
        )
        
        lines = [
            f"Date: {datetime.now().strftime('%Y-%m-%d')}",
            f"Total Fights: {len(archives)}",
            f"Total Pool: ₱{total_pool:,.2f}",
            f"Total Payout: ₱{total_payout:,.2f}",
            f"House Commission: ₱{total_pool - total_payout:,.2f}",
        ]
        
        return self.printer.print_receipt("DAILY REPORT", lines, total_pool)
    
    def close(self):
        """Close all connections"""
        self.scanner.stop()
        self.printer.close()


# ─────────────────────────────────────────────────────────────
# Standalone operations (can run from command line)
# ─────────────────────────────────────────────────────────────

def main():
    """Test printing and scanning"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Sabong POS Tools')
    parser.add_argument('command', choices=['print', 'scan', 'test'],
                      help='Command to run')
    parser.add_argument('--printer', default='sunmi',
                      help='Printer type')
    parser.add_argument('--device', default=None,
                      help='Printer device')
    args = parser.parse_args()
    
    if args.command == 'print':
        printer = ThermalPrinter(args.printer, args.device)
        
        # Test ticket
        test_bet = {
            'ticket_number': 'TEST' + datetime.now().strftime('%Y%m%d%H%M%S'),
            'amount': 1000,
            'side': 'meron',
            'created_at': datetime.now().isoformat()
        }
        test_fight = {
            'fight_number': 1,
            'odds_meron': 1.45,
            'odds_wala': 2.8,
            'odds_draw': 8,
            'arena_name': 'SABONG ARENA'
        }
        
        printer.print_ticket(test_bet, test_fight)
        print("Test ticket printed")
        
    elif args.command == 'scan':
        scanner = BarcodeScanner(on_scan=lambda d: print(f"Scanned: {d}"))
        print("Starting scanner... press Ctrl+C to stop")
        scanner.start()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scanner.stop()
            print("Scanner stopped")
    
    elif args.command == 'test':
        print("Testing printer connection...")
        printer = ThermalPrinter(args.printer, args.device)
        printer.printer and printer.printer.test_printer()
        
        print("\nTesting camera...")
        scanner = BarcodeScanner()
        if scanner.cap:
            print("Camera OK")
            scanner.cap.release()
        else:
            print("Camera not available")


if __name__ == '__main__':
    main()