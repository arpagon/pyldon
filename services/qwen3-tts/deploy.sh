#!/bin/bash
# Auto-deploy Qwen3-TTS: wait for build, then start service
# Run in tmux - monitors build and deploys when ready

set -e
cd /home/arpagon/Workspace/arpagon/pyldon/services/qwen3-tts

echo "=== $(date) === Waiting for Docker build to finish..."

# Wait for the build tmux session to finish
while true; do
    # Check if build.log has BUILD_DONE marker
    if grep -q "===BUILD_DONE===" build.log 2>/dev/null; then
        echo "=== $(date) === Build log has DONE marker"
        break
    fi
    
    # Check if build process is still running (check tmux pane for activity)
    LAST_LINE=$(tail -1 build.log 2>/dev/null)
    echo "  [$(date +%H:%M:%S)] Still building... last: ${LAST_LINE:0:80}"
    sleep 30
done

# Check if build succeeded
if grep -q "Successfully tagged\|Successfully built" build.log 2>/dev/null; then
    echo "=== $(date) === ✅ Build succeeded!"
else
    echo "=== $(date) === ❌ Build may have failed. Check build.log"
    echo "Last 20 lines:"
    tail -20 build.log
    echo ""
    echo "Attempting to start anyway..."
fi

# Start the service
echo "=== $(date) === Starting Qwen3-TTS service..."
docker-compose down 2>/dev/null || true
docker-compose up -d 2>&1
echo "=== $(date) === Container started"

# Wait for health check
echo "=== $(date) === Waiting for service to be healthy (model loading ~60-120s)..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:3011/health > /dev/null 2>&1; then
        echo "=== $(date) === ✅ Qwen3-TTS is healthy!"
        curl -s http://localhost:3011/health | python3 -m json.tool 2>/dev/null
        echo ""
        echo "=== $(date) === 🎉 Service ready!"
        echo "  API:  http://localhost:3011"
        echo "  Docs: http://localhost:3011/docs"
        echo ""
        
        # Show GPU usage
        nvidia-smi | grep -A3 "MiB"
        echo ""
        echo "=== DEPLOY COMPLETE ==="
        exit 0
    fi
    
    # Show container logs periodically
    if (( i % 10 == 0 )); then
        echo "  [$(date +%H:%M:%S)] Still loading... (${i}/60 checks)"
        docker-compose logs --tail=3 2>/dev/null | tail -3
    fi
    sleep 5
done

echo "=== $(date) === ⚠️ Service didn't become healthy in 5 minutes"
echo "Container logs:"
docker-compose logs --tail=30 2>/dev/null
echo ""
echo "Container status:"
docker-compose ps 2>/dev/null
