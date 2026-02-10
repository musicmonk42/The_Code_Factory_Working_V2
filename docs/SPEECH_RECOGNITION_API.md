# Speech Recognition API Documentation

## Overview

The Code Factory Platform supports multiple clarification channels including **voice-based speech recognition** for hands-free requirement clarification. This feature uses the SpeechRecognition library with Google's speech recognition API to convert spoken responses into text.

## Prerequisites

### System Requirements

Speech recognition requires the following system libraries (already installed in Docker):

**Build Time (included in Dockerfile builder stage):**
- `portaudio19-dev` - PortAudio development files for PyAudio compilation
- `libasound2-dev` - ALSA sound system development files  
- `flac` - FLAC command-line tool
- `libflac-dev` - FLAC audio format development files

**Runtime (included in Dockerfile runtime stage):**
- `libportaudio2` - PortAudio runtime library for audio I/O

### Python Dependencies

- `SpeechRecognition>=3.10.0` - Already included in requirements.txt
- `PyAudio` - Automatically installed as a dependency of SpeechRecognition

## API Usage

### Activating Speech Recognition

To use voice-based clarification, set the `channel` parameter to `"voice"` in your clarification request:

```bash
POST /generator/{job_id}/clarify
Content-Type: application/json

{
  "readme_content": "Build a REST API for user management...",
  "channel": "voice"
}
```

### Supported Channels

The `channel` parameter accepts the following values:

| Channel | Description | Status |
|---------|-------------|--------|
| `cli` | Command-line text input (default) | ✅ Available |
| `voice` | Speech recognition via microphone | ✅ Available |
| `gui` | Textual TUI interface | ✅ Available |
| `web` | Web-based form interface | ✅ Available |
| `slack` | Slack webhook integration | ⚙️ Requires config |
| `email` | Email-based interaction | ⚙️ Requires config |
| `sms` | SMS-based interaction | ⚙️ Requires config |

### Full Example

```python
import requests

# Create a job
job_response = requests.post(
    "http://localhost:8080/jobs",
    json={"type": "generation"}
)
job_id = job_response.json()["job_id"]

# Upload README file
files = {"file": open("README.md", "rb")}
requests.post(
    f"http://localhost:8080/generator/upload/{job_id}",
    files=files
)

# Initiate voice-based clarification
clarify_response = requests.post(
    f"http://localhost:8080/generator/{job_id}/clarify",
    json={
        "readme_content": "Build a web application with user authentication",
        "channel": "voice"  # Enable speech recognition
    }
)

# Response will include clarification questions
questions = clarify_response.json()["clarifications"]
print(f"Speak your answers to these {len(questions)} questions:")
for q in questions:
    print(f"  - {q}")
```

## API Response

### Successful Clarification Initiation

```json
{
  "status": "clarification_initiated",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "clarifications": [
    "What type of database would you like to use?",
    "What authentication method should be used?"
  ],
  "confidence": 0.65,
  "questions_count": 2,
  "method": "llm",
  "channel": "voice"
}
```

### Error Responses

**Voice Not Available:**
```json
{
  "status": "error",
  "message": "Speech Recognition not available for VoicePrompt.",
  "error_type": "ValueError"
}
```

**Missing Audio Hardware:**
If the system cannot access the microphone, the server will fall back to CLI mode and log a warning.

## Voice Recognition Flow

1. **API Request**: Client sends clarification request with `channel: "voice"`
2. **Routing**: Request flows through GeneratorService → OmniCore → Clarifier
3. **Channel Setup**: Clarifier initializes VoicePrompt instance
4. **Microphone Access**: System accesses microphone via PyAudio
5. **Speech Capture**: User speaks answer (10-second timeout)
6. **Google API**: Audio sent to Google Speech Recognition
7. **Text Conversion**: Speech converted to text
8. **Processing**: Answer processed by clarifier
9. **Response**: Clarified requirements returned to client

## Configuration

### Environment Variables

Speech recognition uses Google's free speech-to-text API by default. No API key required for basic usage.

**Optional Configuration:**
```bash
# Target language for speech recognition (default: "en")
CLARIFIER_TARGET_LANGUAGE=en

# Interaction mode (default: "cli")
CLARIFIER_INTERACTION_MODE=voice
```

### Microphone Requirements

- **Server Deployment**: Voice channel requires the server to have microphone access
- **Docker**: May require `--device /dev/snd:/dev/snd` for audio device access
- **Kubernetes**: Requires nodeSelector for nodes with audio hardware

## Troubleshooting

### Common Issues

**1. "Speech Recognition not available"**
- Cause: SpeechRecognition library not installed or audio libraries missing
- Solution: Rebuild Docker image with updated Dockerfile

**2. "Could not request results from Google Speech Recognition service"**
- Cause: No internet connection or Google API temporarily unavailable
- Solution: Check network connectivity, will auto-fallback to CLI

**3. "Listening for answer..." hangs**
- Cause: Microphone not detected or permissions issue
- Solution: Verify microphone access, check system audio devices

**4. "Audio input timed out"**
- Cause: No speech detected within 10-second window
- Solution: Speak clearly within timeout period

### Fallback Behavior

The system gracefully degrades if voice recognition fails:
1. Attempts voice recognition
2. On failure, falls back to CLI text input
3. Logs warning with error details
4. Continues clarification process

## Security Considerations

- Audio data is sent to Google's Speech Recognition API
- No audio is stored by the Code Factory Platform
- Consider privacy implications when using speech recognition
- For sensitive projects, use text-based channels (CLI, web)

## Performance

- **Latency**: 1-3 seconds per response (includes audio capture + API call)
- **Accuracy**: Depends on audio quality, accent, and background noise
- **Language Support**: English is primary, see Google Speech API docs for other languages

## Related Documentation

- [Clarifier Architecture](../generator/clarifier/README.md)
- [Docker Configuration](../Dockerfile)
- [API Reference](../openapi_schema.json)
