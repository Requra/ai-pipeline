import os
import sys

# Add the app directory to the path so we can import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.graph.pipeline import graph

def export_graph():
    print("--- Generating Pipeline Architecture Image ---")
    try:
        # Generate the PNG bytes using Mermaid.js
        # draw_mermaid_png() requires pygraphviz or similar but many versions 
        # of LangGraph can use a remote renderer if no local renderer is found
        img_data = graph.get_graph().draw_mermaid_png()
        
        # Save to root directory for easy access
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        output_path = os.path.join(root_dir, "pipeline_arch.png")
        
        with open(output_path, "wb") as f:
            f.write(img_data)
        
        print(f"SUCCESS: Architecture image saved to {output_path}")
    except Exception as e:
        print(f"Error generating image: {e}")
        print("\nFallback (Mermaid.js code):")
        print("Copy the code below and paste it into a Mermaid Live Editor (https://mermaid.live/) to see your graph.")
        print("-" * 50)
        print(graph.get_graph().draw_mermaid())
        print("-" * 50)

if __name__ == "__main__":
    export_graph()
