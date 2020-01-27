// Copyright 2004-present Facebook. All Rights Reserved.

#pragma once

#include <atomic>
#include "defs.h"

extern "C" {
#include <libavformat/avformat.h>
#include <libavformat/avio.h>
#include <libavutil/imgutils.h>
}

namespace ffmpeg {

/**
 * Class uses FFMPEG library to decode one media stream (audio or video).
 */

class Stream {
 public:
  Stream(
      AVFormatContext* inputCtx,
      MediaFormat format,
      bool convertPtsToWallTime);
  virtual ~Stream();

  // returns 0 - on success or negative error
  int openCodec();
  // returns number processed bytes from packet, or negative error
  int decodeFrame(const AVPacket* packet, int* gotFramePtr);
  // returns stream index
  int getIndex() const {
    return format_.stream;
  }
  // returns number decoded/sampled bytes
  int getFrameBytes(DecoderOutputMessage* out, bool headerOnly);
  // returns number decoded/sampled bytes
  int flush(DecoderOutputMessage* out, bool headerOnly);
  // rescale package
  void rescalePackage(AVPacket* packet);
  // return media format
  MediaFormat getMediaFormat() const {
    return format_;
  }

 protected:
  virtual int initFormat() = 0;
  // returns number processed bytes from packet, or negative error
  virtual int analyzePacket(const AVPacket* packet, int* gotFramePtr);
  // returns number decoded/sampled bytes, or negative error
  virtual int copyFrameBytes(ByteStorage* out, bool flush) = 0;
  // initialize codec, returns output buffer size, or negative error
  virtual int estimateBytes(bool flush) = 0;
  // sets output format
  virtual void setHeader(DecoderHeader* header) = 0;
  // finds codec
  virtual AVCodec* findCodec(AVCodecContext* ctx);

 private:
  int fillBuffer(DecoderOutputMessage* out, bool flush, bool headerOnly);

 protected:
  AVFormatContext* const inputCtx_;
  MediaFormat format_;
  const bool convertPtsToWallTime_;

  AVCodecContext* codecCtx_{nullptr};
  AVFrame* frame_{nullptr};

  std::atomic<size_t> numGenerator_{0};
};

} // namespace ffmpeg
