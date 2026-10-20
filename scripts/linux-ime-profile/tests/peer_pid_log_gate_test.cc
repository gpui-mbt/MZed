#include "ipc/peer_pid_log_test.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iostream>
#include <string>
#include <string_view>
#include <sys/wait.h>
#include <unistd.h>

namespace {

std::string CaptureStderr(const std::function<void()> &callback) {
  int pipe_fds[2];
  if (::pipe(pipe_fds) != 0) {
    std::abort();
  }
  const int saved_stderr = ::dup(STDERR_FILENO);
  if (saved_stderr < 0 || ::dup2(pipe_fds[1], STDERR_FILENO) < 0) {
    std::abort();
  }
  ::close(pipe_fds[1]);
  callback();
  std::fflush(stderr);
  if (::dup2(saved_stderr, STDERR_FILENO) < 0) {
    std::abort();
  }
  ::close(saved_stderr);

  std::string output;
  char buffer[256];
  for (;;) {
    const ssize_t count = ::read(pipe_fds[0], buffer, sizeof(buffer));
    if (count == 0) {
      break;
    }
    if (count < 0) {
      std::abort();
    }
    output.append(buffer, static_cast<size_t>(count));
  }
  ::close(pipe_fds[0]);
  return output;
}

bool Expect(std::string_view name, const std::string &actual,
            const std::string &expected) {
  if (actual == expected) {
    return true;
  }
  std::cerr << name << ": expected stderr [" << expected << "], got ["
            << actual << "]\n";
  return false;
}

}  // namespace

int main() {
  bool ok = true;
  const std::string current_pid = std::to_string(static_cast<long>(::getpid()));
  constexpr pid_t kPeerPid = 424242;
#if defined(MOZC_TEST_AUTHENTICATED_PEER_PID)
  const std::string expected_line =
      "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID=424242\n";
#endif

  ::unsetenv("MOZC_TEST_FCITX_EMITTER_PID");
  ok &= Expect("unset emitter", CaptureStderr([] {
                 mozc::ipc_test::MaybeLogAuthenticatedPeerPid(kPeerPid, true);
               }),
               "");

  const char *invalid_values[] = {"", "x", "1x", "-1",
                                  "999999999999999999999999999999999"};
  for (const char *value : invalid_values) {
    ::setenv("MOZC_TEST_FCITX_EMITTER_PID", value, 1);
    ok &= Expect("malformed emitter", CaptureStderr([] {
                   mozc::ipc_test::MaybeLogAuthenticatedPeerPid(kPeerPid,
                                                                 true);
                 }),
                 "");
  }

  const std::string near_miss_values[] = {
      "0" + current_pid,
      "+" + current_pid,
      current_pid + "0",
  };
  for (const std::string &value : near_miss_values) {
    ::setenv("MOZC_TEST_FCITX_EMITTER_PID", value.c_str(), 1);
    ok &= Expect("non-exact emitter pid", CaptureStderr([] {
                   mozc::ipc_test::MaybeLogAuthenticatedPeerPid(kPeerPid,
                                                                 true);
                 }),
                 "");
  }

  ::setenv("MOZC_TEST_FCITX_EMITTER_PID", current_pid.c_str(), 1);
  ok &= Expect("missing server path", CaptureStderr([] {
                 mozc::ipc_test::MaybeLogAuthenticatedPeerPid(kPeerPid, false);
               }),
               "");
  ok &= Expect("invalid peer pid", CaptureStderr([] {
                 mozc::ipc_test::MaybeLogAuthenticatedPeerPid(0, true);
               }),
               "");

  const std::string inherited_child_output = CaptureStderr([&] {
    const pid_t child = ::fork();
    if (child < 0) {
      std::abort();
    }
    if (child == 0) {
      mozc::ipc_test::MaybeLogAuthenticatedPeerPid(kPeerPid, true);
      std::fflush(stderr);
      _exit(0);
    }
    int status = 0;
    if (::waitpid(child, &status, 0) != child || !WIFEXITED(status) ||
        WEXITSTATUS(status) != 0) {
      std::abort();
    }
  });
  ok &= Expect("inherited emitter hint in child", inherited_child_output, "");

  bool errno_preserved = false;
  const std::string valid_output = CaptureStderr([&] {
    errno = EAGAIN;
    mozc::ipc_test::MaybeLogAuthenticatedPeerPid(kPeerPid, true);
    errno_preserved = (errno == EAGAIN);
  });
  if (!errno_preserved) {
    std::cerr << "errno changed during peer-PID observation\n";
    ok = false;
  }

#if defined(MOZC_TEST_AUTHENTICATED_PEER_PID)
  ok &= Expect("target-only payload", valid_output, expected_line);
  if (valid_output != expected_line) {
    std::cerr << "compile-enabled target did not emit the exact marker\n";
    ok = false;
  }
#else
  ok &= Expect("compile-disabled payload", valid_output, "");
  if (!valid_output.empty()) {
    std::cerr << "compile-disabled target emitted a marker\n";
    ok = false;
  }
#endif

  std::cout << (ok ? "peer PID compile gate: PASS\n"
                   : "peer PID compile gate: FAIL\n");
  return ok ? 0 : 1;
}
