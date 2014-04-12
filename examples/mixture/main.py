# Copyright (c) 2014, Salesforce.com, Inc.  All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# - Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# - Neither the name of Salesforce.com nor the names of its contributors
#   may be used to endorse or promote products derived from this
#   software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os
import shutil
import numpy
import scipy
import scipy.misc
from distributions.dbg.random import sample_discrete, sample_discrete_log
from distributions.lp.models.nich import NormalInverseChiSq
from distributions.lp.clustering import PitmanYor
from distributions.lp.mixture import MixtureIdTracker
from distributions.io.stream import json_stream_load, json_stream_dump
import parsable
parsable = parsable.Parsable()


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
RESULTS = os.path.join(ROOT, 'results')
SAMPLES = os.path.join(DATA, 'samples.json.gz')
IMAGE = scipy.lena()


for dirname in [DATA, RESULTS]:
    if not os.path.exists(dirname):
        os.makedirs(dirname)


class ImageModel(object):
    def __init__(self):
        self.clustering = PitmanYor.model_load({
            'alpha': 10.0,
            'd': 0.5,
        })
        self.feature = NormalInverseChiSq.model_load({
            'mu': 0.0,
            'kappa': 1.0,
            'sigmasq': 1.0,
            'nu': 1.0,
        })

    class Mixture(object):
        def __init__(self):
            self.clustering = PitmanYor.Mixture()
            self.feature_x = NormalInverseChiSq.Mixture()
            self.feature_y = NormalInverseChiSq.Mixture()
            self.id_tracker = MixtureIdTracker()

        def __len__(self):
            return len(self.clustering)

        def init_empty(self, model):
            self.clustering.clear()
            self.feature_x.clear()
            self.feature_y.clear()
            self.id_tracker.init()

            # Add a single empty group
            self.clustering.append(0)
            self.feature_x.add_group(model.feature)
            self.feature_y.add_group(model.feature)
            self.id_tracker.add_group()

            self.clustering.init(model.clustering)
            self.feature_x.init(model.feature)
            self.feature_y.init(model.feature)

        def score_value(self, model, xy, scores):
            x, y = xy
            self.clustering.score(model.clustering, scores)
            self.feature_x.score_value(model.feature, x, scores)
            self.feature_y.score_value(model.feature, y, scores)

        def add_value(self, model, groupid, xy):
            x, y = xy
            group_added = self.clustering.add_value(model.clustering, groupid)
            self.feature_x.add_value(model.feature, groupid, x)
            self.feature_y.add_value(model.feature, groupid, y)
            if group_added:
                self.feature_x.add_group(model.feature)
                self.feature_y.add_group(model.feature)
                self.id_tracker.add_group()

        def remove_value(self, model, groupid, xy):
            x, y = xy
            group_removeed = self.clustering.remove_value(
                model.clustering,
                groupid)
            self.feature_x.remove_value(model.feature, groupid, x)
            self.feature_y.remove_value(model.feature, groupid, y)
            if group_removeed:
                self.feature_x.remove_group(model.feature, groupid)
                self.feature_y.remove_group(model.feature, groupid)
                self.id_tracker.remove_group(groupid)


def sample_from_image(image, sample_count):
    image = -1.0 * image
    image -= image.min()
    x_pmf = image.sum(axis=1)
    y_pmfs = image.copy()
    for y_pmf in y_pmfs:
        y_pmf /= y_pmf.sum()

    x_scale = 2.0 / (image.shape[0] - 1)
    y_scale = 2.0 / (image.shape[1] - 1)

    for _ in xrange(sample_count):
        x = sample_discrete(x_pmf)
        y = sample_discrete(y_pmfs[x])
        yield (x * x_scale - 1.0, y * y_scale - 1.0)


def synthesize_image(model, mixture):
    width, height = IMAGE.shape
    image = numpy.zeros((width, height))
    scores = numpy.zeros(len(mixture), dtype=numpy.float32)
    x_scale = 2.0 / (width - 1)
    y_scale = 2.0 / (height - 1)
    for x in xrange(width):
        for y in xrange(height):
            xy = (x * x_scale - 1.0, y * y_scale - 1.0)
            mixture.score_value(model, xy, scores)
            prob = numpy.exp(scores, out=scores).sum()
            image[x, y] = prob

    image /= image.max()
    image -= 1.0
    image *= -255
    return image.astype(numpy.uint8)


@parsable.command
def create_dataset(sample_count=10000):
    '''
    Extract dataset from image.
    '''
    print 'sampling {} points from image'.format(sample_count)
    samples = sample_from_image(IMAGE, sample_count)
    json_stream_dump(samples, SAMPLES)
    scipy.misc.imsave(os.path.join(RESULTS, 'original.png'), IMAGE)


@parsable.command
def compress_sequential():
    '''
    Compress image via sequential initialization.
    '''
    assert os.path.exists(SAMPLES), 'first create dataset'
    print 'compressing sequential initialization'
    model = ImageModel()
    mixture = ImageModel.Mixture()
    mixture.init_empty(model)
    scores = numpy.zeros(1, dtype=numpy.float32)

    for xy in json_stream_load(SAMPLES):
        scores.resize(len(mixture))
        mixture.score_value(model, xy, scores)
        groupid = sample_discrete_log(scores)
        mixture.add_value(model, groupid, xy)

    print 'found {} components'.format(len(mixture))
    image = synthesize_image(model, mixture)
    scipy.misc.imsave(os.path.join(RESULTS, 'sequential.png'), image)


@parsable.command
def compress_gibbs(passes=100):
    '''
    Compress image via gibbs sampling.
    '''
    assert passes > 0
    assert os.path.exists(SAMPLES), 'first create dataset'
    print 'compressing via {} gibbs scans'.format(passes)
    model = ImageModel()
    mixture = ImageModel.Mixture()
    mixture.init_empty(model)
    scores = numpy.zeros(1, dtype=numpy.float32)
    assignments = {}

    for i, xy in enumerate(json_stream_load(SAMPLES)):
        scores.resize(len(mixture))
        mixture.score_value(model, xy, scores)
        groupid = sample_discrete_log(scores)
        mixture.add_value(model, groupid, xy)
        assignments[i] = mixture.id_tracker.packed_to_global(groupid)

    for _ in xrange(passes - 1):
        for i, xy in enumerate(json_stream_load(SAMPLES)):
            groupid = mixture.id_tracker.global_to_packed(assignments[i])
            mixture.remove_value(model, groupid, xy)
            scores.resize(len(mixture))
            mixture.score_value(model, xy, scores)
            groupid = sample_discrete_log(scores)
            mixture.add_value(model, groupid, xy)
            assignments[i] = mixture.id_tracker.packed_to_global(groupid)

    print 'found {} components'.format(len(mixture))
    image = synthesize_image(model, mixture)
    scipy.misc.imsave(os.path.join(RESULTS, 'gibbs.png'), image)


def json_loop_load(filename):
    while True:
        for i, item in enumerate(json_stream_load(filename)):
            yield i, item


def annealing_schedule(passes):
    assert passes > 1
    passes = float(passes)
    add_rate = passes
    remove_rate = passes - 1
    state = add_rate
    while True:
        if state >= 0:
            state -= remove_rate
            yield True
        else:
            state += add_rate
            yield False


@parsable.command
def compress_annealing(passes=100):
    '''
    Compress image via subsample annealing.
    '''
    assert passes > 1
    assert os.path.exists(SAMPLES), 'first create dataset'
    print 'compressing via {} subsample annealing passes'.format(passes)
    model = ImageModel()
    mixture = ImageModel.Mixture()
    mixture.init_empty(model)
    scores = numpy.zeros(1, dtype=numpy.float32)
    assignments = {}

    to_add = json_loop_load(SAMPLES)
    to_remove = json_loop_load(SAMPLES)

    for next_action_is_add in annealing_schedule(passes):
        if next_action_is_add:
            i, xy = to_add.next()
            if i in assignments:
                break
            scores.resize(len(mixture))
            mixture.score_value(model, xy, scores)
            groupid = sample_discrete_log(scores)
            mixture.add_value(model, groupid, xy)
            assignments[i] = mixture.id_tracker.packed_to_global(groupid)
        else:
            i, xy = to_remove.next()
            groupid = mixture.id_tracker.global_to_packed(assignments.pop(i))
            mixture.remove_value(model, groupid, xy)

    print 'found {} components'.format(len(mixture))
    image = synthesize_image(model, mixture)
    scipy.misc.imsave(os.path.join(RESULTS, 'annealing.png'), image)


@parsable.command
def clean():
    '''
    Clean out dataset and results.
    '''
    for dirname in [DATA, RESULTS]:
        if not os.path.exists(dirname):
            shutil.rmtree(dirname)


@parsable.command
def run(sample_count=10000, passes=100):
    '''
    Generate all datasets and run all algorithms.
    '''
    create_dataset(sample_count)
    compress_sequential()
    compress_gibbs(passes)
    compress_annealing(passes)


if __name__ == '__main__':
    parsable.dispatch()
