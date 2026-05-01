#!/usr/bin/env python3
"""
WoW Raid Recorder - Main Entry Point
Automatically records and names WoW raid encounters based on combat logs.
"""

import time
import argparse
from pathlib import Path

from config_manager import ConfigManager
from obs_client import OBSClient
from state_manager import RecordingState
from combat_parser.parser import CombatParser
from log_watcher import LogMonitor

from constants import (
    DEFAULT_OBS_HOST,
    DEFAULT_OBS_PORT,
    DEFAULT_RENAME_DELAY,
    DEFAULT_MIN_RECORDING_DURATION,
    DEFAULT_DUNGEON_TIMEOUT,
    LOG_PREFIXES,
    ERROR_MESSAGES,
)


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='WoW Raid Recorder - Automatic raid encounter recording',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Run with default config
  %(prog)s --config myconfig.ini  # Use custom config file
  %(prog)s --show-config      # Show config and exit
  %(prog)s --create-config    # Create default config and exit
  %(prog)s --no-rename        # Disable auto-renaming
        """
    )
    
    parser.add_argument('--config', '-c', type=str, 
                       help='Path to configuration file')
    parser.add_argument('--show-config', action='store_true',
                       help='Show current configuration and exit')
    parser.add_argument('--no-rename', action='store_true',
                       help='Disable auto-renaming of recordings')
    parser.add_argument('--create-config', action='store_true',
                       help='Create a default config file and exit')
    
    return parser.parse_args()


def validate_configuration(config):
    """Validate configuration and return any errors."""
    errors = {}
    
    # Check log directory
    if not config.LOG_DIR.exists():
        errors.setdefault('General', []).append(
            f"{ERROR_MESSAGES['LOG_DIR_NOT_FOUND']}: {config.LOG_DIR}"
        )
    
    # Check OBS connection settings
    if not config.OBS_HOST:
        errors.setdefault('OBS', []).append("OBS host cannot be empty")
    
    # Check recording extension
    if not config.RECORDING_EXTENSION.startswith('.'):
        errors.setdefault('General', []).append(
            f"Recording extension must start with '.': {config.RECORDING_EXTENSION}"
        )
    
    return errors


def print_startup_info(config, obs_settings):
    """Print startup information and configuration."""
    print("\n" + "="*60)
    print("WoW Raid Recorder - Starting up")
    print("="*60)
    
    print(f"\n📁 Log Directory: {config.LOG_DIR}")
    
    if obs_settings and 'record_directory' in obs_settings:
        print(f"🎥 OBS Recording Directory: {obs_settings['record_directory']}")
    elif config.RECORDING_PATH_FALLBACK:
        print(f"🎥 Fallback Recording Path: {config.RECORDING_PATH_FALLBACK}")
    
    print(f"\n⚙️  Configuration:")
    print(f"  • Auto-rename: {'ENABLED' if config.AUTO_RENAME else 'DISABLED'}")
    if config.AUTO_RENAME:
        print(f"    - Rename delay: {config.RENAME_DELAY}s")
        print(f"    - Min duration: {config.MIN_RECORDING_DURATION}s")
        print(f"    - Delete short recordings: {'YES' if config.DELETE_SHORT_RECORDINGS else 'NO'}")
    
    print(f"\n🎯 Enabled Difficulties:")
    print(f"  • LFR: {'✓' if config.RECORD_LFR else '✗'}")
    print(f"  • Normal: {'✓' if config.RECORD_NORMAL else '✗'}")
    print(f"  • Heroic: {'✓' if config.RECORD_HEROIC else '✗'}")
    print(f"  • Mythic: {'✓' if config.RECORD_MYTHIC else '✗'}")
    print(f"  • Other: {'✓' if config.RECORD_OTHER else '✗'}")
    print(f"  • M+: {'✓' if config.RECORD_MPLUS else '✗'}")
    
    print(f"\n📝 File Naming: YYYY-MM-DD_HH-MM-SS_BossName_Difficulty{config.RECORDING_EXTENSION}")
    print("="*60 + "\n")


def initialize_components(config):
    """Initialize and connect all components."""
    
    # Initialize OBS client
    obs_client = OBSClient(
        host=config.OBS_HOST,
        port=config.OBS_PORT,
        password=config.OBS_PASSWORD
    )
    
    # Connect to OBS
    try:
        print(f"{LOG_PREFIXES['RECORDER']} 🔌 Connecting to OBS...")
        if not obs_client.connect():
            raise ConnectionError(ERROR_MESSAGES['OBS_CONNECTION_FAILED'])
        
        # Get recording settings
        obs_settings = obs_client.get_recording_settings()
        return obs_client, obs_settings
        
    except Exception as e:
        print(f"{LOG_PREFIXES['RECORDER']} ❌ {ERROR_MESSAGES['OBS_CONNECTION_FAILED']}: {e}")
        raise


def print_troubleshooting_tips():
    """Print troubleshooting tips for OBS connection issues."""
    print(f"\n{LOG_PREFIXES['RECORDER']} 🔧 Troubleshooting tips:")
    print("1. Ensure OBS Studio is running")
    print("2. Enable OBS WebSocket server:")
    print("   • Tools → WebSocket Server Settings")
    print("   • Check 'Enable WebSocket server'")
    print("   • Note the Server Port (default: 4455)")
    print("   • Set Password if desired (leave empty for none)")
    print("3. Verify the settings in your config file match OBS")
    print("4. Restart OBS after changing WebSocket settings")


def main():
    """Main application entry point."""
    args = parse_arguments()
    
    # Handle --create-config flag
    if args.create_config:
        config_path = Path(args.config) if args.config else None
        config = ConfigManager(config_path)
        print(f"✅ Configuration file created at: {config.config_path}")
        print("Please edit the file and run the program again.")
        return
    
    # Initialize configuration
    config = ConfigManager(Path(args.config) if args.config else None)
    
    # Apply command line overrides
    if args.no_rename:
        config.set('Recording', 'auto_rename', 'false')
        print(f"{LOG_PREFIXES['CONFIG']} Auto-rename disabled via command line")
    
    # Show configuration if requested
    if args.show_config:
        config.print_summary()
        return
    
    # Validate configuration
    errors = validate_configuration(config)
    if errors:
        print(f"{LOG_PREFIXES['CONFIG']} ❌ Configuration errors found:")
        for section, section_errors in errors.items():
            print(f"\n  [{section}]")
            for error in section_errors:
                print(f"    • {error}")
        print("\nPlease fix these errors and try again.")
        return
    
    # Initialize OBS connection
    try:
        obs_client, obs_settings = initialize_components(config)
    except Exception as e:
        print_troubleshooting_tips()
        return
    
    # Initialize remaining components
    state_manager = RecordingState()
    parser = CombatParser(obs_client, state_manager, config)
    
    # Print startup information
    print_startup_info(config, obs_settings)
    
    # Initialize and start log monitor
    try:
        log_monitor = LogMonitor(config.LOG_DIR, parser)
        log_monitor.start()
        
        if not log_monitor.is_monitoring():
            print(f"{LOG_PREFIXES['MONITOR']} ❌ Failed to start log monitoring")
            return
            
        print(f"{LOG_PREFIXES['RECORDER']} ✅ Ready! Waiting for raid encounters... (Press Ctrl+C to stop)\n")
        
    except Exception as e:
        print(f"{LOG_PREFIXES['MONITOR']} ❌ Failed to start log monitor: {e}")
        obs_client.disconnect()
        return
    
    # Main event loop
    try:
        while True:
            time.sleep(1)
            
            # Optional: Print periodic status (every 30 seconds)
            # You can enable this for debugging
            # if int(time.time()) % 30 == 0:
            #     status = log_monitor.get_status()
            #     print(f"{LOG_PREFIXES['STATUS']} Monitoring: {status['is_monitoring']}, "
            #           f"Tailing: {status['is_tailing']}")
            
    except KeyboardInterrupt:
        print(f"\n\n{LOG_PREFIXES['RECORDER']} 🛑 Shutdown requested...")
    finally:
        # Clean shutdown
        print(f"\n{LOG_PREFIXES['RECORDER']} " + "="*60)
        print(f"{LOG_PREFIXES['RECORDER']} Cleaning up...")
        print(f"{LOG_PREFIXES['RECORDER']} " + "="*60)
        
        # Stop log monitoring
        if 'log_monitor' in locals():
            log_monitor.stop()
        
        # Stop parser threads
        if 'parser' in locals():
            parser.shutdown()
        
        # Disconnect from OBS
        if 'obs_client' in locals():
            obs_client.disconnect()
        
        print(f"{LOG_PREFIXES['RECORDER']} ✅ Cleanup complete")
        print(f"{LOG_PREFIXES['RECORDER']} " + "="*60)


if __name__ == "__main__":
    main()