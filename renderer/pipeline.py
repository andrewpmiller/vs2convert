"""
Headless render verification for VS-2 shaders.

Optional module - requires moderngl + numpy + Pillow.
"""

from typing import Dict


def run_verification(shader_content: str, shader_name: str) -> dict:
    """
    Run headless render verification on a VS-2 shader.
    
    Args:
        shader_content: Full .frag file content
        shader_name: Name of the shader
        
    Returns:
        dict with keys:
            - passed: bool
            - errors: list[str]
            - metrics: dict
    """
    try:
        import moderngl
        import numpy as np
        from PIL import Image
        
        # Create virtual framebuffer
        ctx = moderngl.create_standalone_context()
        framebuffer = moderngl.Framebuffer(width=640, height=480, color_attachments=1)
        color_texture = moderngl.Texture(width=640, height=480, components=4, kind='f')
        framebuffer.attach(color_attachments=[('color', color_texture)])
        
        # Compile shader
        vs = ctx.compile_shader("""
            #version 300 es
            in vec4 vertexPosition;
            out vec2 texCoord;
            void main() {
                texCoord = vertexPosition.xy * 0.5 + 0.5;
                gl_Position = vertexPosition;
            }
        """)
        
        fs = ctx.compile_shader(shader_content)
        
        # Create program
        program = ctx.program(vs, fs)
        
        # Create vertex buffer
        vbo = ctx.buffer(np.zeros((4, 4), dtype=np.float32))
        vao = ctx.simple_vertex_buffer(vbo, program, 'vertexPosition', dtype='2f')
        
        # Render
        color_texture.bind(0)
        program['resolution'] = (640.0, 480.0)
        program['time'] = 0.0
        framebuffer.use()
        
        for i in range(4):
            vao.render(mode='TRIANGLES', instance=i)
        
        # Read back
        color_texture.unbind()
        rgba = np.frombuffer(color_texture.read(), dtype=np.uint8)
        
        # Check for valid output
        errors = []
        if rgba.size == 0:
            errors.append("No output data")
        else:
            # Check that we have valid pixel data
            if np.all(rgba == 0):
                errors.append("All pixels are black (shader may not render)")
            elif np.any(rgba > 0):
                # Good - some pixels rendered
                pass
        
        ctx.release()
        
        return {
            "passed": len(errors) == 0,
            "errors": errors,
            "metrics": {
                "resolution": (640, 480),
                "pixels_rendered": rgba.size > 0
            }
        }
        
    except ImportError:
        return {
            "passed": False,
            "errors": ["moderngl + numpy + Pillow required for render verification"],
            "metrics": {}
        }
    except Exception as e:
        return {
            "passed": False,
            "errors": [f"Render verification failed: {str(e)}"],
            "metrics": {}
        }
