from abc import ABCMeta, abstractmethod


class Protocol(object, metaclass=ABCMeta):
    """Parent class enforcing common interface for all protocols."""
    @abstractmethod
    def generate(self):
        pass

    @abstractmethod
    def to_dict(self):
        pass

