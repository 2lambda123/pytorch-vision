// Copyright 2004-present Facebook. All Rights Reserved.

#include "decoder.h"
#include <c10/util/Logging.h>
#include <future>
#include <iostream>
#include <mutex>
#include "audio_stream.h"
#include "cc_stream.h"
#include "subtitle_stream.h"
#include "util.h"
#include "video_stream.h"

namespace ffmpeg {

namespace {

constexpr ssize_t kMinSeekBufferSize = 1024;
constexpr ssize_t kMaxSeekBufferSize = 4 * 1024;
constexpr size_t kIoBufferSize = 4 * 1024;
constexpr size_t kLogBufferSize = 1024;

int ffmpeg_lock(void** mutex, enum AVLockOp op) {
  std::mutex** handle = (std::mutex**)mutex;
  switch (op) {
    case AV_LOCK_CREATE:
      *handle = new std::mutex();
      break;
    case AV_LOCK_OBTAIN:
      (*handle)->lock();
      break;
    case AV_LOCK_RELEASE:
      (*handle)->unlock();
      break;
    case AV_LOCK_DESTROY:
      delete *handle;
      break;
  }
  return 0;
}

bool mapFfmpegType(AVMediaType media, MediaType* type) {
  switch (media) {
    case AVMEDIA_TYPE_AUDIO:
      *type = TYPE_AUDIO;
      return true;
    case AVMEDIA_TYPE_VIDEO:
      *type = TYPE_VIDEO;
      return true;
    case AVMEDIA_TYPE_SUBTITLE:
      *type = TYPE_SUBTITLE;
      return true;
    case AVMEDIA_TYPE_DATA:
      *type = TYPE_CC;
      return true;
    default:
      return false;
  }
}

std::unique_ptr<Stream> createStream(
    MediaType type,
    AVFormatContext* ctx,
    int idx,
    bool convertPtsToWallTime,
    const FormatUnion& format,
    int64_t loggingUuid) {
  switch (type) {
    case TYPE_AUDIO:
      return std::make_unique<AudioStream>(
          ctx, idx, convertPtsToWallTime, format.audio);
    case TYPE_VIDEO:
      return std::make_unique<VideoStream>(
          // negative loggingUuid indicates video streams.
          ctx,
          idx,
          convertPtsToWallTime,
          format.video,
          -loggingUuid);
    case TYPE_SUBTITLE:
      return std::make_unique<SubtitleStream>(
          ctx, idx, convertPtsToWallTime, format.subtitle);
    case TYPE_CC:
      return std::make_unique<CCStream>(
          ctx, idx, convertPtsToWallTime, format.subtitle);
    default:
      return nullptr;
  }
}

} // Namespace

/* static */
void Decoder::logFunction(void* avcl, int level, const char* cfmt, va_list vl) {
  if (!avcl) {
    // Nothing can be done here
    return;
  }

  AVClass* avclass = *reinterpret_cast<AVClass**>(avcl);
  if (!avclass) {
    // Nothing can be done here
    return;
  }
  Decoder* decoder = nullptr;
  if (strcmp(avclass->class_name, "AVFormatContext") == 0) {
    AVFormatContext* context = reinterpret_cast<AVFormatContext*>(avcl);
    if (context) {
      decoder = reinterpret_cast<Decoder*>(context->opaque);
    }
  } else if (strcmp(avclass->class_name, "AVCodecContext") == 0) {
    AVCodecContext* context = reinterpret_cast<AVCodecContext*>(avcl);
    if (context) {
      decoder = reinterpret_cast<Decoder*>(context->opaque);
    }
  } else if (strcmp(avclass->class_name, "AVIOContext") == 0) {
    AVIOContext* context = reinterpret_cast<AVIOContext*>(avcl);
    // only if opaque was assigned to Decoder pointer
    if (context && context->read_packet == Decoder::readFunction) {
      decoder = reinterpret_cast<Decoder*>(context->opaque);
    }
  } else if (strcmp(avclass->class_name, "SWResampler") == 0) {
    // expect AVCodecContext as parent
    if (avclass->parent_log_context_offset) {
      AVClass** parent =
          *(AVClass***)(((uint8_t*)avcl) + avclass->parent_log_context_offset);
      AVCodecContext* context = reinterpret_cast<AVCodecContext*>(parent);
      if (context) {
        decoder = reinterpret_cast<Decoder*>(context->opaque);
      }
    }
  } else if (strcmp(avclass->class_name, "SWScaler") == 0) {
    // cannot find a way to pass context pointer through SwsContext struct
  } else {
    VLOG(2) << "Unknown context class: " << avclass->class_name;
  }

  if (decoder != nullptr && decoder->enableLogLevel(level)) {
    char buf[kLogBufferSize] = {0};
    // Format the line
    int* prefix = decoder->getPrintPrefix();
    *prefix = 1;
    av_log_format_line(avcl, level, cfmt, vl, buf, sizeof(buf) - 1, prefix);
    // pass message to the decoder instance
    std::string msg(buf);
    decoder->logCallback(level, msg);
  }
}

bool Decoder::enableLogLevel(int level) const {
  return ssize_t(level) <= params_.logLevel;
}

void Decoder::logCallback(int level, const std::string& message) {
  LOG(INFO) << "Msg, level: " << level << ", msg: " << message;
}

/* static */
int Decoder::shutdownFunction(void* ctx) {
  Decoder* decoder = (Decoder*)ctx;
  if (decoder == nullptr) {
    return 1;
  }
  return decoder->shutdownCallback();
}

int Decoder::shutdownCallback() {
  return interrupted_ ? 1 : 0;
}

/* static */
int Decoder::readFunction(void* opaque, uint8_t* buf, int size) {
  Decoder* decoder = reinterpret_cast<Decoder*>(opaque);
  if (decoder == nullptr) {
    return 0;
  }
  return decoder->readCallback(buf, size);
}

/* static */
int64_t Decoder::seekFunction(void* opaque, int64_t offset, int whence) {
  Decoder* decoder = reinterpret_cast<Decoder*>(opaque);
  if (decoder == nullptr) {
    return -1;
  }
  return decoder->seekCallback(offset, whence);
}

int Decoder::readCallback(uint8_t* buf, int size) {
  return seekableBuffer_.read(buf, size, params_.timeoutMs);
}

int64_t Decoder::seekCallback(int64_t offset, int whence) {
  return seekableBuffer_.seek(offset, whence, params_.timeoutMs);
}

/* static */
void Decoder::initOnce() {
  static std::once_flag flagInit;
  std::call_once(flagInit, []() {
    av_register_all();
    avcodec_register_all();
    avformat_network_init();
    // register ffmpeg lock manager
    av_lockmgr_register(&ffmpeg_lock);
    av_log_set_callback(Decoder::logFunction);
    av_log_set_level(AV_LOG_ERROR);
    LOG(INFO) << "Registered ffmpeg libs";
  });
}

Decoder::Decoder() {
  initOnce();
}

Decoder::~Decoder() {
  cleanUp();
}

bool Decoder::init(const DecoderParameters& params, DecoderInCallback&& in) {
  cleanUp();

  if ((params.uri.empty() || in) && (!params.uri.empty() || !in)) {
    LOG(ERROR) << "Either external URI gets provided"
               << " or explicit input callback";
    return false;
  }

  // set callback and params
  params_ = params;

  auto tmpCtx = avformat_alloc_context();

  if (!tmpCtx) {
    LOG(ERROR) << "Cannot allocate format context";
    return false;
  }

  AVInputFormat* fmt = nullptr;
  if (in) {
    const size_t avioCtxBufferSize = kIoBufferSize;
    uint8_t* avioCtxBuffer = (uint8_t*)av_malloc(avioCtxBufferSize);
    if (!avioCtxBuffer) {
      LOG(ERROR) << "av_malloc cannot allocate " << avioCtxBufferSize
                 << " bytes";
      avformat_close_input(&tmpCtx);
      cleanUp();
      return false;
    }

    bool canSeek = in(nullptr, 0, 0) == 0;

    if (!seekableBuffer_.init(
            std::forward<DecoderInCallback>(in),
            kMinSeekBufferSize,
            kMaxSeekBufferSize,
            params_.timeoutMs)) {
      LOG(ERROR) << "seekable buffer initialization failed";
      av_free(avioCtxBuffer);
      avformat_close_input(&tmpCtx);
      cleanUp();
      return false;
    }

    if (params_.isImage) {
      const char* fmtName = "image2";
      switch (seekableBuffer_.getImageType()) {
        case ImageType::JPEG:
          fmtName = "jpeg_pipe";
          break;
        case ImageType::PNG:
          fmtName = "png_pipe";
          break;
        case ImageType::TIFF:
          fmtName = "tiff_pipe";
          break;
        default:
          break;
      }

      fmt = av_find_input_format(fmtName);
    }

    if (!(avioCtx_ = avio_alloc_context(
              avioCtxBuffer,
              avioCtxBufferSize,
              0,
              reinterpret_cast<void*>(this),
              &Decoder::readFunction,
              nullptr,
              canSeek ? &Decoder::seekFunction : nullptr))) {
      LOG(ERROR) << "avio_alloc_context failed";
      av_free(avioCtxBuffer);
      avformat_close_input(&tmpCtx);
      cleanUp();
      return false;
    }

    tmpCtx->pb = avioCtx_;
  }

  interrupted_ = false;
  // ffmpeg avformat_open_input call can hang if media source doesn't respond
  // set a guard for handle such situations
  std::promise<bool> p;
  std::future<bool> f = p.get_future();
  std::thread guard([&f, this]() {
    auto timeout = std::chrono::milliseconds(params_.timeoutMs);
    if (std::future_status::timeout == f.wait_for(timeout)) {
      LOG(ERROR) << "Cannot open stream within " << params_.timeoutMs << " ms";
      interrupted_ = true;
    }
  });

  tmpCtx->opaque = reinterpret_cast<void*>(this);
  tmpCtx->interrupt_callback.callback = Decoder::shutdownFunction;
  tmpCtx->interrupt_callback.opaque = reinterpret_cast<void*>(this);

  // add network timeout
  tmpCtx->flags |= AVFMT_FLAG_NONBLOCK;

  AVDictionary* options = nullptr;
  av_dict_set_int(&options, "analyzeduration", params_.timeoutMs * 1000, 0);
  av_dict_set_int(&options, "stimeout", params_.timeoutMs * 1000, 0);
  if (params_.listen) {
    av_dict_set_int(&options, "listen", 1, 0);
  }

  int result = 0;
  if (fmt) {
    result = avformat_open_input(&tmpCtx, nullptr, fmt, &options);
  } else {
    result =
        avformat_open_input(&tmpCtx, params_.uri.c_str(), nullptr, &options);
  }
  av_dict_free(&options);

  p.set_value(true);
  guard.join();

  inputCtx_ = tmpCtx;

  if (result < 0 || interrupted_) {
    LOG(ERROR) << "avformat_open_input failed, error: "
               << Util::generateErrorDesc(result);
    cleanUp();
    return false;
  }

  result = avformat_find_stream_info(inputCtx_, nullptr);

  if (result < 0) {
    LOG(ERROR) << "avformat_find_stream_info failed, error: "
               << Util::generateErrorDesc(result);
    cleanUp();
    return false;
  }

  if (!activateStreams()) {
    LOG(ERROR) << "Cannot activate streams";
    cleanUp();
    return false;
  }

  onInit();

  if (params.startOffsetMs != 0) {
    av_seek_frame(
        inputCtx_,
        -1,
        params.startOffsetMs * AV_TIME_BASE / 1000,
        AVSEEK_FLAG_FRAME | AVSEEK_FLAG_ANY);
  }

  LOG(INFO) << "Decoder initialized, log level: " << params_.logLevel;
  outOfRange_ = false;
  return true;
}

bool Decoder::activateStreams() {
  for (int i = 0; i < inputCtx_->nb_streams; i++) {
    // - find the corespondent format at params_.formats set
    MediaFormat format;
    const auto media = inputCtx_->streams[i]->codec->codec_type;
    if (!mapFfmpegType(media, &format.type)) {
      VLOG(1) << "Stream media: " << media << " at index " << i
              << " gets ignored, unknown type";

      continue; // unsupported type
    }

    // check format
    auto it = params_.formats.find(format);
    if (it == params_.formats.end()) {
      VLOG(1) << "Stream type: " << format.type << " at index: " << i
              << " gets ignored, caller is not interested";
      continue; // clients don't care about this media format
    }

    // do we have stream of this type?
    auto stream = findByType(format);

    // should we process this stream?

    if (it->stream == -2 || // all streams of this type are welcome
        (!stream && (it->stream == -1 || it->stream == i))) { // new stream
      VLOG(1) << "Stream type: " << format.type << " found, at index: " << i;
      auto stream = createStream(
          format.type,
          inputCtx_,
          i,
          params_.convertPtsToWallTime,
          it->format,
          params_.loggingUuid);
      CHECK(stream);
      if (stream->openCodec() < 0) {
        LOG(ERROR) << "Cannot open codec " << i;
        return false;
      }
      streams_.emplace(i, std::move(stream));
    }
  }

  return true;
}

void Decoder::shutdown() {
  cleanUp();
}

void Decoder::interrupt() {
  interrupted_ = true;
}

void Decoder::cleanUp() {
  if (!interrupted_) {
    interrupted_ = true;
  }

  if (inputCtx_) {
    for (auto& stream : streams_) {
      // Drain stream buffers.
      DecoderOutputMessage msg;
      while (msg.payload = createByteStorage(0),
             stream.second->flush(&msg, params_.headerOnly) > 0) {
      }
      stream.second.reset();
    }
    streams_.clear();
    avformat_close_input(&inputCtx_);
  }
  if (avioCtx_) {
    av_freep(&avioCtx_->buffer);
    av_freep(&avioCtx_);
  }

  // reset callback
  seekableBuffer_.shutdown();
}

int Decoder::getBytes(size_t workingTimeInMs) {
  if (outOfRange_) {
    return ENODATA;
  }
  // decode frames until cache is full and leave thread
  // once decode() method gets called and grab some bytes
  // run this method again
  // init package
  AVPacket avPacket;
  av_init_packet(&avPacket);
  avPacket.data = nullptr;
  avPacket.size = 0;

  auto end = std::chrono::steady_clock::now() +
      std::chrono::milliseconds(workingTimeInMs);
  // return true if elapsed time less than timeout
  auto watcher = [end]() -> bool {
    return std::chrono::steady_clock::now() <= end;
  };

  int result = ETIMEDOUT;
  size_t decodingErrors = 0;
  while (!interrupted_ && watcher()) {
    result = av_read_frame(inputCtx_, &avPacket);
    if (result == AVERROR(EAGAIN)) {
      VLOG(4) << "Decoder is busy...";
      result = 0; // reset error, EAGAIN is not an error at all
      break;
    } else if (result == AVERROR_EOF) {
      flushStreams();
      VLOG(1) << "End of stream";
      result = ENODATA;
      break;
    } else if (result < 0) {
      flushStreams();
      LOG(ERROR) << "Error detected: " << Util::generateErrorDesc(result);
      break;
    }

    // get stream
    auto stream = findByIndex(avPacket.stream_index);
    if (stream == nullptr) {
      av_packet_unref(&avPacket);
      continue;
    }

    stream->rescalePackage(&avPacket);

    AVPacket copyPacket = avPacket;

    size_t numConsecutiveNoBytes = 0;
    // it can be only partial decoding of the package bytes
    do {
      // decode package
      if ((result = processPacket(stream, &copyPacket)) < 0) {
        break;
      }

      if (result == 0 && params_.maxProcessNoBytes != 0 &&
          ++numConsecutiveNoBytes > params_.maxProcessNoBytes) {
        LOG(ERROR) << "Exceeding max amount of consecutive no bytes";
        break;
      }
      if (result > 0) {
        numConsecutiveNoBytes = 0;
      }

      copyPacket.size -= result;
      copyPacket.data += result;
    } while (copyPacket.size > 0);

    // post loop check
    if (result < 0) {
      if (params_.maxPackageErrors != 0 && // check errors
          ++decodingErrors >= params_.maxPackageErrors) { // reached the limit
        break;
      }
    } else {
      decodingErrors = 0; // reset on success
    }

    result = 0;

    av_packet_unref(&avPacket);
  }

  av_packet_unref(&avPacket);

  return result;
}

Stream* Decoder::findByIndex(int streamIndex) const {
  auto it = streams_.find(streamIndex);
  return it != streams_.end() ? it->second.get() : nullptr;
}

Stream* Decoder::findByType(const MediaFormat& format) const {
  for (auto& stream : streams_) {
    if (stream.second->getMediaFormat().type == format.type) {
      return stream.second.get();
    }
  }
  return nullptr;
}

int Decoder::processPacket(Stream* stream, AVPacket* packet) {
  // decode package
  int gotFrame = 0;
  int result;
  DecoderOutputMessage msg;
  msg.payload = createByteStorage(0);
  if ((result = stream->decodeFrame(packet, &gotFrame)) >= 0 && gotFrame &&
      stream->getFrameBytes(&msg, params_.headerOnly) > 0) {
    // check end offset
    if (params_.endOffsetMs <= 0 ||
        !(outOfRange_ = msg.header.pts > params_.endOffsetMs * 1000)) {
      push(std::move(msg));
    }
  }
  return result;
}

void Decoder::flushStreams() {
  VLOG(1) << "Flushing streams...";
  for (auto& stream : streams_) {
    DecoderOutputMessage msg;
    while (msg.payload = createByteStorage(0),
           stream.second->flush(&msg, params_.headerOnly) > 0) {
      // check end offset
      if (params_.endOffsetMs <= 0 ||
          !(outOfRange_ = msg.header.pts > params_.endOffsetMs * 1000)) {
        push(std::move(msg));
      }
    }
  }
}

int Decoder::decode_all(const DecoderOutCallback& callback) {
  int result;
  do {
    DecoderOutputMessage out;
    if (0 == (result = decode(&out, params_.timeoutMs))) {
      callback(std::move(out));
    }
  } while (result == 0);
  return result;
}
} // namespace ffmpeg
