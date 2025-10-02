# Hardware requirements

<table>
  <tr>
    <td valign="top"><b>STORAGE</b></td>
    <td>At least 10 times the corpus size, min 10GB. For example, if your unzipped training data is 10GB, make sure you have at least 100GB on drive.</td>
  </tr>
  <tr>
    <td valign="top"><b>PLATFORM</b></td>
    <td>Ubuntu 22.04 LTS (x86_64) is the reference platform. Ubuntu 20.04 or newer distributions are also supported.</td>
  </tr>
  <tr>
    <td valign="top"><b>CPU</b></td>
    <td>No minimum hardware specifications are required. Thus, we suggest a least a 8-cores CPU for training and decoding.</td>
  </tr>
  <tr>
    <td valign="top"><b>GPU</b></td>
    <td>At least one <a href="https://developer.nvidia.com/cuda-gpus">CUDA-capable GPU</a> with minimum 8GB of internal memory. ModernMT is tested with NVIDIA RTX 40-series GPUs (including RTX&nbsp;4080) using CUDA&nbsp;12.1. Multiple GPUs are recommended in order to speed up both training and decoding.</td>
  </tr>
  <tr>
    <td valign="top"><b>RAM</b></td>
    <td>Minimum 16GB, highly depending on parallel data during training</td>
  </tr>
</table>

ModernMT's Python dependencies are pinned to versions that are known to work with CUDA&nbsp;12.1, PyTorch 2.1+, and Fairseq 0.12.2+. Check `requirements.txt` (and `requirements_cuda-11.txt` for legacy environments) to confirm the expected ranges when preparing your system.

# Modern NVIDIA GPUs (RTX 40 Series)

ModernMT now bundles dependency versions that ship official CUDA&nbsp;12.1 wheels for PyTorch and Fairseq, ensuring full compatibility with NVIDIA Ada Lovelace GPUs such as the RTX&nbsp;4080. No manual patches are required.

1. **Install the latest NVIDIA driver.** On Ubuntu you can let the operating system pick the recommended driver for your GPU:

   ```bash
   sudo apt update
   sudo ubuntu-drivers install
   sudo reboot
   ```

2. **Install the CUDA Toolkit and cuDNN runtime (if you are not using Docker).** Follow the steps in [Install NVIDIA drivers and CUDA Toolkit](#install-nvidia-drivers-and-cuda-toolkit) to install CUDA&nbsp;12.1 and the corresponding cuDNN packages.

3. **Install GPU-enabled PyTorch before the rest of the Python dependencies.** Use the official CUDA&nbsp;12.1 wheel index so that the packages downloaded by `pip` are compatible with your driver:

   ```bash
   pip3 install --upgrade pip
   pip3 install --index-url https://download.pytorch.org/whl/cu121 torch torchvision torchaudio
   pip3 install -r requirements.txt
   ```

4. **Verify the installation.** The following command should report PyTorch 2.1+ and Fairseq 0.12.2+ with CUDA available:

   ```bash
   python3 - <<'PY'
   import torch, fairseq
   print(f"PyTorch: {torch.__version__}, CUDA available: {torch.cuda.is_available()}")
   print(f"Fairseq: {fairseq.__version__}")
   PY
   ```

When these steps complete successfully, ModernMT is ready to train and serve models on an RTX&nbsp;4080 or other RTX 40-series GPUs.

# Install ModernMT via Docker

If you are familiar with Docker, this is usually the easiest option to use ModernMT. This section assumes you have already a running instance of Docker, if this is not the case please [follow these instructions](https://docs.docker.com/install/linux/docker-ce/ubuntu/) in order to properly install Docker.

### Install NVIDIA drivers

Install the latest recommended NVIDIA driver for your GPU and reboot:
```bash
sudo apt update
sudo ubuntu-drivers install
sudo reboot
```

### Install NVIDIA Docker
Next install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) so Docker containers can access the GPU:
```bash
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Run latest ModernMT image
Finally, you are able to run the latest ModernMT image with Docker:
```bash
sudo docker run --gpus all --rm -it --publish 8045:8045 modernmt/master bash
```

Done! Go to [README.md](README.md) to create your first engine.


# Install ModernMT from binaries

With every ModernMT release in Github we also include a binary version of the package that can be used directly without the need to compile the source code.

### Install NVIDIA drivers and CUDA Toolkit

First you need to install the **NVIDIA drivers**:
```bash
sudo apt update
sudo ubuntu-drivers install
sudo reboot
```

After rebooting, install the **CUDA Toolkit 12.1** using the official NVIDIA apt repository for Ubuntu 22.04 (adjust the repository URL if you are on a different supported distribution):
```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get install -y cuda-toolkit-12-1
```

Next install the **NVIDIA cuDNN library** from [NVIDIA cuDNN Downloads](https://developer.nvidia.com/cudnn-downloads). Select the CUDA&nbsp;12.x runtime and development Debian packages that match your operating system, download them, and install them for example with:
```bash
sudo dpkg -i libcudnn9-cuda-12-1_*.deb
sudo dpkg -i libcudnn9-dev-cuda-12-1_*.deb
```
Refer to the release notes on the download page for the exact package names of the latest cuDNN build.

### Install Java 11 and Python 3

ModernMT requires Java 11 and Python 3.8 (or higher). If not already installed on your system, you can run the following command:
```bash
sudo apt install -y openjdk-11-jdk python3 python3-pip
```

In order to check if the installation completed successfully you can run these two commands and check the output:
```bash
$ java -version
openjdk version "11.0.x" 202x-xx-xx
[...]

$  python3 --version
Python 3.8.x
```

If your output is not the expected one, please go to the [Troubleshooting](#troubleshooting-and-support) section of this guide.

### Download ModernMT

Download the latest ModernMT binary file from [ModernMT releases page](https://github.com/modernmt/modernmt/releases) and extract the archive:
```bash
tar xvfz mmt-<version-number>-ubuntu.tar.gz
rm mmt-*.tar.gz
cd mmt
```

Finally install the python dependencies:
```bash
pip3 install -r requirements.txt
```

Done! Go to [README.md](README.md) to create your first engine.


# Install ModernMT from source

This option is most suitable for developers, contributors or enthusiasts willing to work with the bleeding-edge development code, before a stable release. Installing ModernMT from source in fact gives you the ability to keep your code continously up-to-date and modify the source code yourself.

### Common installation steps

Please, follow these installation steps from the previous option (binary installation):
- [Install NVIDIA drivers and CUDA Toolkit](#install-nvidia-drivers-and-cuda-toolkit)
- [Install Java 11 and Python 3](#install-java-11-and-python-3)

### Install development libraries and tools

Next install Git, Maven, CMake and Boost together with few more c++ libraries with the following command:
```bash
sudo apt install -y git maven cmake libboost-all-dev zlib1g-dev libbz2-dev
```

### Clone ModernMT repository from GitHub

We are now ready to clone the ModernMT repository from Github:
```bash
git clone https://github.com/modernmt/modernmt.git modernmt && cd modernmt
```

Before compiling, ensure you followed the steps in [Modern NVIDIA GPUs (RTX 40 Series)](#modern-nvidia-gpus-rtx-40-series) so that GPU-enabled PyTorch and Fairseq are available in your environment.

Next, run the installation script:
```bash
python3 setup.py
```

Finally you can compile the source code with maven:
```bash
pushd src
mvn clean install
popd
```

You have now a working instance of ModernMT. Go to [README.md](README.md) to create your first engine.


# Troubleshooting and support

### "Too many open files" errors when runnning ModernMT

For performance reasons ModernMT does not limit the maximum number of open files. This could lead to errors reporting too many open files, or max open file limit reached.

In order to avoid this error, set the option `nofile` in `/etc/security/limits.conf` to a high limit and restart the machine, for example:
```
* soft nofile 1048576
* hard nofile 1048576
```

### Wrong version of Java

First, check your Java version with the following command:
```bash
java -version
```

If the first line report a version of Java different from 1.8, you need to **update default Java version**.
In order to do so, just run the command:
```bash
sudo update-alternatives --config java
```

and type the number of the option that contains **java-8-openjdk**, then press ENTER. Here's an example:

```
$ sudo update-alternatives --config java
There are 2 choices for the alternative java (providing /usr/bin/java).

  Selection    Path                                            Priority   Status
------------------------------------------------------------
* 0            /usr/lib/jvm/java-7-openjdk-amd64/jre/bin/java   1071      auto mode
  1            /usr/lib/jvm/java-7-openjdk-amd64/jre/bin/java   1071      manual mode
  2            /usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java   1069      manual mode

Press enter to keep the current choice[*], or type selection number: 2
```

### Support

Our [GitHub issues page](https://github.com/ModernMT/MMT/issues) is the best option to search for solutions to your problems or open new issues regarding the code.
For customizations and enterprise support, please contact us at info@modernmt.eu .
