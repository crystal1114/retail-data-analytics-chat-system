import { useCallback, useRef, useState } from 'react';
import { transcribeAudio } from '../api';

export type MicStatus = 'idle' | 'recording' | 'transcribing';

interface UseMicRecorderOptions {
  onTranscript: (text: string) => void;
  onError?: (msg: string) => void;
}

const PREFERRED_MIME = 'audio/webm;codecs=opus';

function pickMime(): string {
  if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(PREFERRED_MIME)) {
    return PREFERRED_MIME;
  }
  return '';
}

export function useMicRecorder({ onTranscript, onError }: UseMicRecorderOptions) {
  const [status, setStatus] = useState<MicStatus>('idle');
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (status !== 'idle') return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
    } catch {
      onError?.('Microphone permission denied');
      return;
    }

    chunksRef.current = [];
    const mime = pickMime();
    const recorder = new MediaRecorder(streamRef.current!, mime ? { mimeType: mime } : undefined);

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    recorder.onstop = async () => {
      stopStream();
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
      if (blob.size === 0) {
        setStatus('idle');
        return;
      }

      setStatus('transcribing');
      try {
        const text = await transcribeAudio(blob);
        if (text.trim()) onTranscript(text.trim());
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Transcription failed';
        onError?.(msg);
      } finally {
        setStatus('idle');
      }
    };

    recorderRef.current = recorder;
    recorder.start();
    setStatus('recording');
  }, [status, onTranscript, onError, stopStream]);

  const stop = useCallback(() => {
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop();
    }
  }, []);

  const toggle = useCallback(() => {
    if (status === 'recording') stop();
    else if (status === 'idle') start();
  }, [status, start, stop]);

  return { status, toggle, start, stop };
}
