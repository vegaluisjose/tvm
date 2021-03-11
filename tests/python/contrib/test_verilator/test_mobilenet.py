# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import tvm
from tvm import te, relay, transform
from tvm.contrib.download import download_testdata
from tvm.contrib import graph_runtime as runtime

import os
from PIL import Image
import numpy as np

from test_verilator.infrastructure import (
    skip_test,
    compile_hardware,
    compiler_opts,
    offload,
)


def extract(path):
    import tarfile

    if path.endswith("tgz") or path.endswith("gz"):
        dir_path = os.path.dirname(path)
        tar = tarfile.open(path)
        tar.extractall(path=dir_path)
        tar.close()
    else:
        raise RuntimeError("Could not decompress the file: " + path)


def get_real_image(im_height, im_width):
    repo_base = "https://github.com/dmlc/web-data/raw/master/tensorflow/models/InceptionV1/"
    img_name = "elephant-299.jpg"
    image_url = os.path.join(repo_base, img_name)
    img_path = download_testdata(image_url, img_name, module="data")
    image = Image.open(img_path).resize((im_height, im_width))
    x = np.array(image).astype("uint8")
    data = np.reshape(x, (1, im_height, im_width, 3))
    return data


def get_mobilenet_model():
    model_url = "https://storage.googleapis.com/download.tensorflow.org/models/mobilenet_v1_2018_08_02/mobilenet_v1_1.0_224_quant.tgz"
    model_path = download_testdata(
        model_url, "mobilenet_v1_1.0_224_quant.tgz", module=["tf", "official"]
    )
    model_dir = os.path.dirname(model_path)
    extract(model_path)
    tflite_model_file = os.path.join(
        model_dir, "mobilenet_v1_1.0_224_quant.tflite"
    )
    tflite_model_buf = open(tflite_model_file, "rb").read()
    try:
        import tflite

        return tflite.Model.GetRootAsModel(tflite_model_buf, 0)
    except AttributeError:
        import tflite.Model

        return tflite.Model.Model.GetRootAsModel(tflite_model_buf, 0)


def get_input_tensor_name():
    return "input"


def compile_model_to_relay(model):
    input_tensor = get_input_tensor_name()
    input_shape = (1, 224, 224, 3)
    input_dtype = "uint8"
    mod, params = relay.frontend.from_tflite(
        model,
        shape_dict={input_tensor: input_shape},
        dtype_dict={input_tensor: input_dtype},
    )
    return mod, params


def run_model(mod, params=None, opts=None):
    with transform.PassContext(
        opt_level=3, config={"relay.ext.verilator.options": opts}
    ):
        lib = relay.build(mod, target="llvm", params=params)
    module = runtime.GraphModule(lib["default"](tvm.cpu()))
    image_data = get_real_image(224, 224)
    input_tensor = get_input_tensor_name()
    module.set_input(input_tensor, image_data)
    module.run()
    return module.get_output(0).asnumpy()


def get_labels():
    label_file_url = "".join(
        [
            "https://raw.githubusercontent.com/",
            "tensorflow/tensorflow/master/tensorflow/lite/java/demo/",
            "app/src/main/assets/",
            "labels_mobilenet_quant_v1_224.txt",
        ]
    )
    label_file = "labels_mobilenet_quant_v1_224.txt"
    label_path = download_testdata(label_file_url, label_file, module="data")
    # List of 1001 classes
    with open(label_path) as f:
        labels = f.readlines()
    return labels


def check_result(res):
    labels = get_labels()
    predictions = np.squeeze(res)
    prediction = np.argmax(predictions)
    # 387 is an elephant
    tvm.testing.assert_allclose(prediction, 387, rtol=1e-5, atol=1e-5)


def tmobilenet(lanes):
    if skip_test():
        return
    model = get_mobilenet_model()
    mod, params = compile_model_to_relay(model)
    mod = offload(mod)
    lib = compile_hardware(lanes)
    opts = compiler_opts(lib)
    res = run_model(mod, params, opts)
    check_result(res)


def test_mobilenet_vector_1():
    tmobilenet(1)
