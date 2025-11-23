import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

# Add the self_fixing_engineer directory so arbiter can be imported
sys.path.insert(0, os.path.join(project_root, 'self_fixing_engineer'))

# Add omnicore_engine directory
sys.path.insert(0, os.path.join(project_root, 'omnicore_engine'))

# Add generator directory
sys.path.insert(0, os.path.join(project_root, 'generator'))