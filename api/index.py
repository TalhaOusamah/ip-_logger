import os
import sys

# Project ke root folder ko Python import path mein add karta hai
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app
