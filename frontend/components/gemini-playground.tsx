'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Mic, StopCircle, Video, Monitor } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { base64ToFloat32Array, float32ToPcm16 } from '@/lib/utils';

interface Config {
  systemPrompt: string;
  voice: string;
  googleSearch: boolean;
  allowInterruptions: boolean;
}

export default function NutriChatRealtime() {
  // Configuração fixa do systemPrompt para um nutricionista brasileiro altamente graduado
  const [config, setConfig] = useState<Config>({
    systemPrompt: "Você é um nutricionista brasileiro altamente graduado, com ampla experiência em nutrição clínica e esportiva. Forneça conselhos nutricionais precisos, baseados em evidências científicas e adaptados à realidade do Brasil. Seja cordial, objetivo e engajado, ajudando o paciente a alcançar uma vida mais saudável.",
    voice: "Puck",
    googleSearch: true,
    allowInterruptions: false,
  });
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioInputRef = useRef<any>(null);
  const clientId = useRef(crypto.randomUUID());
  const [videoEnabled, setVideoEnabled] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoStreamRef = useRef<MediaStream | null>(null);
  const videoIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const [chatMode, setChatMode] = useState<'audio' | 'video' | null>(null);
  const [videoSource, setVideoSource] = useState<'camera' | 'screen' | null>(null);

  const voices = ["Puck", "Charon", "Kore", "Fenrir", "Aoede"];
  let audioBuffer: Float32Array[] = [];
  let isPlaying = false;

  const startStream = async (mode: 'audio' | 'camera' | 'screen') => {
    setChatMode(mode === 'audio' ? 'audio' : 'video');

    wsRef.current = new WebSocket(`ws://localhost:8000/ws/${clientId.current}`);
    
    wsRef.current.onopen = async () => {
      wsRef.current?.send(JSON.stringify({
        type: 'config',
        config: config,
      }));
      
      await startAudioStream();

      if (mode !== 'audio') {
        setVideoEnabled(true);
        setVideoSource(mode);
      }

      setIsStreaming(true);
      setIsConnected(true);
    };

    wsRef.current.onmessage = async (event) => {
      const response = JSON.parse(event.data);
      if (response.type === 'audio') {
        const audioData = base64ToFloat32Array(response.data);
        playAudioData(audioData);
      } else if (response.type === 'text') {
        setText(prev => prev + response.text + '\n');
      }
    };

    wsRef.current.onerror = (err: any) => {
      setError('Erro no WebSocket: ' + err.message);
      setIsStreaming(false);
    };

    wsRef.current.onclose = () => {
      setIsStreaming(false);
    };
  };

  // Inicializa o stream de áudio
  const startAudioStream = async () => {
    try {
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: 16000 // Necessário para o Gemini
      });

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const source = audioContextRef.current.createMediaStreamSource(stream);
      const processor = audioContextRef.current.createScriptProcessor(512, 1, 1);
      
      processor.onaudioprocess = (e) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          const pcmData = float32ToPcm16(inputData);
          const base64Data = btoa(String.fromCharCode(...new Uint8Array(pcmData.buffer)));
          wsRef.current.send(JSON.stringify({
            type: 'audio',
            data: base64Data
          }));
        }
      };

      source.connect(processor);
      processor.connect(audioContextRef.current.destination);
      
      audioInputRef.current = { source, processor, stream };
      setIsStreaming(true);
    } catch (err: any) {
      setError('Erro ao acessar o microfone: ' + err.message);
    }
  };

  // Para o streaming
  const stopStream = () => {
    if (audioInputRef.current) {
      const { source, processor, stream } = audioInputRef.current;
      source.disconnect();
      processor.disconnect();
      stream.getTracks().forEach((track: MediaStreamTrack) => track.stop());
      audioInputRef.current = null;
    }

    if (chatMode === 'video') {
      setVideoEnabled(false);
      setVideoSource(null);
      if (videoStreamRef.current) {
        videoStreamRef.current.getTracks().forEach(track => track.stop());
        videoStreamRef.current = null;
      }
      if (videoIntervalRef.current) {
        clearInterval(videoIntervalRef.current);
        videoIntervalRef.current = null;
      }
    }

    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setIsStreaming(false);
    setIsConnected(false);
    setChatMode(null);
  };

  const playAudioData = async (audioData: Float32Array) => {
    audioBuffer.push(audioData);
    if (!isPlaying) {
      playNextInQueue();
    }
  };

  const playNextInQueue = async () => {
    if (!audioContextRef.current || audioBuffer.length === 0) {
      isPlaying = false;
      return;
    }
    isPlaying = true;
    const audioData = audioBuffer.shift()!;
    const buffer = audioContextRef.current.createBuffer(1, audioData.length, 24000);
    buffer.copyToChannel(audioData, 0);
    const source = audioContextRef.current.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContextRef.current.destination);
    source.onended = () => {
      playNextInQueue();
    };
    source.start();
  };

  // Inicia o vídeo quando habilitado
  useEffect(() => {
    if (videoEnabled && videoRef.current) {
      const startVideo = async () => {
        try {
          let stream: MediaStream;
          if (videoSource === 'camera') {
            stream = await navigator.mediaDevices.getUserMedia({
              video: { width: { ideal: 320 }, height: { ideal: 240 } }
            });
          } else if (videoSource === 'screen') {
            stream = await navigator.mediaDevices.getDisplayMedia({
              video: { width: { ideal: 1920 }, height: { ideal: 1080 } }
            });
          }
          videoRef.current.srcObject = stream;
          videoStreamRef.current = stream;
          videoIntervalRef.current = setInterval(() => {
            captureAndSendFrame();
          }, 1000);
        } catch (err: any) {
          console.error('Erro ao iniciar vídeo:', err);
          setError('Erro ao acessar câmera/tela: ' + err.message);
          setChatMode(null);
          stopStream();
          setVideoEnabled(false);
          setVideoSource(null);
        }
      };

      startVideo();
      return () => {
        if (videoStreamRef.current) {
          videoStreamRef.current.getTracks().forEach(track => track.stop());
          videoStreamRef.current = null;
        }
        if (videoIntervalRef.current) {
          clearInterval(videoIntervalRef.current);
          videoIntervalRef.current = null;
        }
      };
    }
  }, [videoEnabled, videoSource]);

  const captureAndSendFrame = () => {
    if (!canvasRef.current || !videoRef.current || !wsRef.current) return;
    const context = canvasRef.current.getContext('2d');
    if (!context) return;
    canvasRef.current.width = videoRef.current.videoWidth;
    canvasRef.current.height = videoRef.current.videoHeight;
    context.drawImage(videoRef.current, 0, 0);
    const base64Image = canvasRef.current.toDataURL('image/jpeg').split(',')[1];
    wsRef.current.send(JSON.stringify({
      type: 'image',
      data: base64Image
    }));
  };

  // Cleanup ao desmontar o componente
  useEffect(() => {
    return () => {
      stopStream();
    };
  }, []);

  return (
    <div className="container mx-auto py-8 px-4">
      <div className="space-y-6">
        <h1 className="text-4xl font-bold tracking-tight">NutriChat - Atendimento Nutricional em Tempo Real</h1>
        
        {error && (
          <Alert variant="destructive">
            <AlertTitle>Erro</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* Não é exibido o System Prompt, pois o perfil já está definido */}

        <Card>
          <CardContent className="pt-6 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="voice-select">Voz</Label>
              <Select
                value={config.voice}
                onValueChange={(value) => setConfig(prev => ({ ...prev, voice: value }))}
                disabled={isConnected}
              >
                <SelectTrigger id="voice-select">
                  <SelectValue placeholder="Selecione uma voz" />
                </SelectTrigger>
                <SelectContent>
                  {voices.map((voice) => (
                    <SelectItem key={voice} value={voice}>
                      {voice}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="google-search"
                checked={config.googleSearch}
                onCheckedChange={(checked) => 
                  setConfig(prev => ({ ...prev, googleSearch: checked as boolean }))
                }
                disabled={isConnected}
              />
              <Label htmlFor="google-search">Ativar busca no Google</Label>
            </div>
          </CardContent>
        </Card>

        <div className="flex gap-4">
          {!isStreaming && (
            <>
              <Button
                onClick={() => startStream('audio')}
                disabled={isStreaming}
                className="gap-2"
              >
                <Mic className="h-4 w-4" />
                Iniciar Atendimento (Áudio)
              </Button>

              <Button
                onClick={() => startStream('camera')}
                disabled={isStreaming}
                className="gap-2"
              >
                <Video className="h-4 w-4" />
                Iniciar Atendimento (Vídeo)
              </Button>
              
              <Button
                onClick={() => startStream('screen')}
                disabled={isStreaming}
                className="gap-2"
              >
                <Monitor className="h-4 w-4" />
                Iniciar Atendimento (Tela)
              </Button>
            </>
          )}

          {isStreaming && (
            <Button
              onClick={stopStream}
              variant="destructive"
              className="gap-2"
            >
              <StopCircle className="h-4 w-4" />
              Encerrar Atendimento
            </Button>
          )}
        </div>

        {isStreaming && (
          <Card>
            <CardContent className="flex items-center justify-center h-24 mt-6">
              <div className="flex flex-col items-center gap-2">
                <Mic className="h-8 w-8 text-blue-500 animate-pulse" />
                <p className="text-gray-600">Ouvindo...</p>
              </div>
            </CardContent>
          </Card>
        )}

        {chatMode === 'video' && (
          <Card>
            <CardContent className="pt-6 space-y-4">
              <div className="flex justify-between items-center">
                <h2 className="text-lg font-semibold">Entrada de Vídeo</h2>
              </div>
              <div className="relative aspect-video bg-black rounded-lg overflow-hidden">
                <video
                  ref={videoRef}
                  autoPlay
                  playsInline
                  muted
                  width={320}
                  height={240}
                  className="w-full h-full object-contain"
                  style={{ transform: videoSource === 'camera' ? 'scaleX(-1)' : 'none' }}
                />
                <canvas
                  ref={canvasRef}
                  className="hidden"
                  width={640}
                  height={480}
                />
              </div>
            </CardContent>
          </Card>
        )}

        {text && (
          <Card>
            <CardContent className="pt-6">
              <h2 className="text-lg font-semibold mb-2">Conversa:</h2>
              <pre className="whitespace-pre-wrap text-gray-700">{text}</pre>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
