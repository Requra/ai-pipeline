import subprocess
import sys

def main():
    print("🎨 Starting LangGraph Studio dev server via script...")
    try:
        # Run langgraph dev using subprocess to forward command line args
        subprocess.run(["langgraph", "dev"] + sys.argv[1:])
    except KeyboardInterrupt:
        print("\nStopping LangGraph Studio dev server.")
        sys.exit(0)
    except FileNotFoundError:
        print("Error: 'langgraph' CLI tool is not installed or not in PATH.")
        sys.exit(1)

if __name__ == "__main__":
    main()
